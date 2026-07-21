from __future__ import annotations

import argparse
import sys
from pathlib import Path

from twiffarchiver import __version__
from twiffarchiver.auth import (
    AuthError,
    Credentials,
    credentials_path,
    load_credentials,
    load_from_browser,
    save_credentials,
)
from twiffarchiver.export import iso_now, make_run_dir, write_meta, write_users
from twiffarchiver.models import FetchMeta
from twiffarchiver.query_ids import discover_query_ids
from twiffarchiver.x_client import XClient, XClientError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twiffarchiver",
        description="Archive X (Twitter) following and follower lists to JSON.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="Manage session cookies")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)

    auth_set = auth_sub.add_parser("set", help="Save auth_token and ct0")
    auth_set.add_argument("--auth-token", help="Value of the auth_token cookie")
    auth_set.add_argument("--ct0", help="Value of the ct0 cookie")
    auth_set.add_argument(
        "--from-browser",
        metavar="BROWSER",
        nargs="?",
        const="chrome",
        help="Load cookies from a local browser (chrome|firefox|edge). "
        "Requires optional dependency: pip install 'twiffarchiver[browser]'",
    )

    auth_sub.add_parser("status", help="Show whether credentials are configured")

    fetch = sub.add_parser("fetch", help="Fetch following/followers for an account")
    fetch.add_argument("account", help="Target account, e.g. @hogehoge or hogehoge")
    fetch.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Output root directory (default: ./out)",
    )
    fetch.add_argument(
        "--following-only",
        action="store_true",
        help="Fetch following list only",
    )
    fetch.add_argument(
        "--followers-only",
        action="store_true",
        help="Fetch followers list only",
    )
    fetch.add_argument(
        "--request-gap",
        type=float,
        default=1.0,
        help="Minimum seconds between API requests (default: 1.0)",
    )

    qids = sub.add_parser("query-ids", help="Refresh cached GraphQL query IDs")
    qids.add_argument("--refresh", action="store_true", help="Force rediscovery")

    return parser


def _cmd_auth_set(args: argparse.Namespace) -> int:
    try:
        if args.from_browser:
            creds = load_from_browser(args.from_browser)
        else:
            token = args.auth_token
            ct0 = args.ct0
            if not token:
                token = input("auth_token: ").strip()
            if not ct0:
                ct0 = input("ct0: ").strip()
            if not token or not ct0:
                print("auth_token and ct0 are required.", file=sys.stderr)
                return 1
            creds = Credentials(auth_token=token, ct0=ct0)
        path = save_credentials(creds)
        print(f"Saved credentials to {path}")
        return 0
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_auth_status() -> int:
    try:
        load_credentials()
        print(f"OK - credentials loaded ({credentials_path()})")
        return 0
    except AuthError as exc:
        print(f"NOT SET - {exc}")
        return 1


def _cmd_query_ids(refresh: bool) -> int:
    ids = discover_query_ids(force=refresh)
    for name, qid in sorted(ids.items()):
        print(f"{name}: {qid}")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    if args.following_only and args.followers_only:
        print("Use only one of --following-only / --followers-only", file=sys.stderr)
        return 1

    do_following = not args.followers_only
    do_followers = not args.following_only

    try:
        creds = load_credentials()
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    started = iso_now()
    following: list = []
    followers: list = []

    try:
        with XClient(creds, request_gap=args.request_gap) as client:
            target = client.resolve_user(args.account)
            print(
                f"Target: @{target.account_name} "
                f"({target.display_name}) id={target.user_id}"
            )

            def progress(label: str):
                def _cb(page: int, total: int) -> None:
                    print(f"  [{label}] page {page}, collected {total}", flush=True)

                return _cb

            if do_following:
                print("Fetching following...")
                following = client.fetch_list(
                    "following", target.user_id, on_page=progress("following")
                )
                print(f"  following: {len(following)}")

            if do_followers:
                print("Fetching followers...")
                followers = client.fetch_list(
                    "followers", target.user_id, on_page=progress("followers")
                )
                print(f"  followers: {len(followers)}")

    except XClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    finished = iso_now()
    run_dir = make_run_dir(args.out, target.account_name)
    if do_following:
        write_users(run_dir / "following.json", following)
    if do_followers:
        write_users(run_dir / "followers.json", followers)

    meta = FetchMeta(
        target_user_id=target.user_id,
        target_account_name=target.account_name,
        target_display_name=target.display_name,
        started_at=started,
        finished_at=finished,
        following_count=len(following) if do_following else -1,
        followers_count=len(followers) if do_followers else -1,
        tool_version=__version__,
    )
    write_meta(run_dir / "meta.json", meta)
    print(f"Wrote {run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "auth":
        if args.auth_command == "set":
            return _cmd_auth_set(args)
        if args.auth_command == "status":
            return _cmd_auth_status()
    if args.command == "fetch":
        return _cmd_fetch(args)
    if args.command == "query-ids":
        return _cmd_query_ids(args.refresh)

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
