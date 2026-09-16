"""
MailLaunch CLI entry point.

    maillaunch auth gmail
    maillaunch auth microsoft
    maillaunch send --provider gmail --csv recipients.csv --subject "..." --body-file template.txt
    maillaunch status
    maillaunch resume
    maillaunch log
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from .auth import gmail_auth, microsoft_auth
from .campaign.csv_parser import CsvParseError, parse_csv
from .campaign.send_log import SendLogger
from .campaign.state import StateStore
from .config import Config, load_config
from .sender.engine import EngineConfig, SendEngine


def _echo_err(msg: str) -> None:
    click.echo(click.style(msg, fg="red"), err=True)


@click.group()
@click.option("--config", "config_path", default=None, help="Path to maillaunch.config.yaml")
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]):
    """MailLaunch -- a CLI cold-email sender for Gmail and Microsoft 365."""
    ctx.ensure_object(dict)
    try:
        ctx.obj["config"] = load_config(config_path)
    except ValueError as e:
        _echo_err(str(e))
        sys.exit(1)
    ctx.obj["state"] = StateStore()
    ctx.obj["logger"] = SendLogger()


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

@cli.group()
def auth():
    """Authenticate with an email provider and store tokens securely."""


@auth.command("gmail")
@click.option("--client-id", default=None, help="Overrides gmail.client_id from config")
@click.pass_context
def auth_gmail(ctx: click.Context, client_id: Optional[str]):
    cfg: Config = ctx.obj["config"]
    cid = client_id or cfg.gmail.client_id
    if not cid:
        _echo_err(
            "No Gmail client_id found. Pass --client-id or set gmail.client_id "
            "in maillaunch.config.yaml."
        )
        sys.exit(1)
    click.echo("Opening browser for Gmail consent...")
    gmail_auth.interactive_login(cid, cfg.gmail_client_secret)
    click.echo(click.style("Gmail authenticated and token stored securely.", fg="green"))


@auth.command("microsoft")
@click.option("--client-id", default=None, help="Overrides microsoft.client_id from config")
@click.pass_context
def auth_microsoft(ctx: click.Context, client_id: Optional[str]):
    cfg: Config = ctx.obj["config"]
    cid = client_id or cfg.microsoft.client_id
    if not cid:
        _echo_err(
            "No Microsoft client_id found. Pass --client-id or set "
            "microsoft.client_id in maillaunch.config.yaml."
        )
        sys.exit(1)
    microsoft_auth.interactive_login(cid)
    click.echo(click.style("Microsoft 365 authenticated and token stored securely.", fg="green"))


# --------------------------------------------------------------------------
# send / resume (shared implementation)
# --------------------------------------------------------------------------

def _build_send_fn(provider: str):
    """Returns (send_fn, refresh_fn) for the given provider, both
    closed over freshly-loaded credentials."""
    if provider == "gmail":
        from .sender import gmail_sender

        creds = gmail_auth.load_credentials()
        if creds is None:
            _echo_err("Not authenticated with Gmail. Run `maillaunch auth gmail` first.")
            sys.exit(1)

        state = {"creds": creds}

        def send_fn(to_email: str, subject: str, body: str):
            gmail_sender.send(state["creds"], to_email, subject, body)

        def refresh_fn() -> bool:
            refreshed = gmail_auth.refresh_or_none(state["creds"])
            if refreshed is None:
                return False
            state["creds"] = refreshed
            return True

        return send_fn, refresh_fn

    elif provider == "microsoft":
        from .sender import graph_sender

        token = microsoft_auth.load_access_token()
        if token is None:
            _echo_err(
                "Not authenticated with Microsoft 365. Run `maillaunch auth microsoft` first."
            )
            sys.exit(1)

        state = {"token": token}

        def send_fn(to_email: str, subject: str, body: str):
            graph_sender.send(state["token"], to_email, subject, body)

        def refresh_fn() -> bool:
            new_token = microsoft_auth.load_access_token()
            if new_token is None:
                return False
            state["token"] = new_token
            return True

        return send_fn, refresh_fn

    else:
        _echo_err(f"Unknown provider: {provider} (expected 'gmail' or 'microsoft')")
        sys.exit(1)


def _run_campaign(ctx: click.Context, campaign_id: str, subject_template: str, body_template: str, provider: str):
    cfg: Config = ctx.obj["config"]
    state: StateStore = ctx.obj["state"]
    logger: SendLogger = ctx.obj["logger"]

    send_fn, refresh_fn = _build_send_fn(provider)
    engine = SendEngine(
        state=state,
        logger=logger,
        config=EngineConfig(
            daily_limit=cfg.daily_limit,
            min_delay_seconds=cfg.send.min_delay_seconds,
            max_delay_seconds=cfg.send.max_delay_seconds,
            retry_attempts=cfg.send.retry_attempts,
            retry_failed=cfg.send.retry_failed,
        ),
        send_fn=send_fn,
        refresh_fn=refresh_fn,
    )

    click.echo(f"Running campaign {campaign_id} via {provider}...")
    summary = engine.run(campaign_id, subject_template, body_template)
    click.echo(
        f"Done. sent={summary['sent']} failed={summary['failed']} "
        f"skipped={summary['skipped']} pending={summary['pending']}"
    )
    if summary["pending"] > 0:
        click.echo(
            click.style(
                "Campaign not fully complete (daily limit reached or interrupted). "
                "Run `maillaunch resume` to continue.",
                fg="yellow",
            )
        )


@cli.command()
@click.option("--provider", type=click.Choice(["gmail", "microsoft"]), required=True)
@click.option("--csv", "csv_path", required=True, type=click.Path(exists=True))
@click.option("--subject", required=True, help="Subject template, supports {{variable}}")
@click.option("--body-file", required=True, type=click.Path(exists=True))
@click.option("--email-column", default=None, help="Override auto-detected email column")
@click.option("--min-delay", "min_delay", type=int, default=None)
@click.option("--max-delay", "max_delay", type=int, default=None)
@click.pass_context
def send(
    ctx: click.Context,
    provider: str,
    csv_path: str,
    subject: str,
    body_file: str,
    email_column: Optional[str],
    min_delay: Optional[int],
    max_delay: Optional[int],
):
    """Start a new send campaign."""
    cfg: Config = ctx.obj["config"]
    state: StateStore = ctx.obj["state"]

    if min_delay is not None:
        cfg.send.min_delay_seconds = min_delay
    if max_delay is not None:
        cfg.send.max_delay_seconds = max_delay

    try:
        recipients = parse_csv(csv_path, email_column)
    except CsvParseError as e:
        _echo_err(str(e))
        sys.exit(1)

    body_template = Path(body_file).read_text(encoding="utf-8")

    campaign_id = state.create_campaign(
        provider=provider,
        csv_path=csv_path,
        subject_template=subject,
        body_template=body_template,
        email_column=email_column,
        recipients=recipients,
    )
    click.echo(f"Created campaign {campaign_id} with {len(recipients)} recipients.")

    _run_campaign(ctx, campaign_id, subject, body_template, provider)


@cli.command()
@click.argument("campaign_id", required=False)
@click.pass_context
def resume(ctx: click.Context, campaign_id: Optional[str]):
    """Resume an interrupted campaign (defaults to the most recent incomplete one)."""
    state: StateStore = ctx.obj["state"]

    if campaign_id:
        record = state.get_campaign(campaign_id)
    else:
        record = state.latest_incomplete_campaign()

    if record is None:
        _echo_err("No incomplete campaign found to resume.")
        sys.exit(1)
    if record.status == "COMPLETE":
        click.echo(f"Campaign {record.id} is already complete. Nothing to resume.")
        return

    _run_campaign(ctx, record.id, record.subject_template, record.body_template, record.provider)


@cli.command()
@click.pass_context
def status(ctx: click.Context):
    """Show today's send count and the current campaign's queue status."""
    cfg: Config = ctx.obj["config"]
    state: StateStore = ctx.obj["state"]

    sent_today = state.sent_count_today()
    click.echo(f"Sent today: {sent_today} / {cfg.daily_limit}")

    record = state.latest_incomplete_campaign()
    if record is None:
        click.echo("No campaign currently in progress.")
        return

    summary = state.campaign_summary(record.id)
    click.echo(
        f"Latest campaign: {record.id} ({record.provider}) -- "
        f"sent={summary['sent']} failed={summary['failed']} "
        f"skipped={summary['skipped']} pending={summary['pending']}"
    )


@cli.command()
@click.option("--date", "date_str", default=None, help="YYYY-MM-DD, defaults to today")
@click.pass_context
def log(ctx: click.Context, date_str: Optional[str]):
    """Print the send log for a given day (defaults to today)."""
    logger: SendLogger = ctx.obj["logger"]

    if date_str:
        from datetime import datetime

        when = datetime.strptime(date_str, "%Y-%m-%d")
        content = logger.read_for_date(when)
    else:
        content = logger.read_today()

    if not content:
        click.echo("No log entries found for that day.")
        return
    click.echo(content, nl=False)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
