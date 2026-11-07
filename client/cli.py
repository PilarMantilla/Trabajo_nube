"""Command line interface for the DFSha prototype."""
from __future__ import annotations

import click
import shlex

try:
    from .dfs_client import DFSClient
except ImportError:  # Support `python client/cli.py` as well as `python -m client.cli`.
    from dfs_client import DFSClient


@click.group()
@click.option("--host", default="localhost", show_default=True, help="Control-plane host")
@click.option("--port", default=8100, show_default=True, type=int, help="Control-plane host port")
@click.option("--user", "username", required=True, help="User account")
@click.option("--password", prompt=True, hide_input=True, help="Password")
@click.option("--scheme", type=click.Choice(["http", "https"]), default=None, help="Override DFS_SCHEME")
@click.pass_context
def cli(ctx: click.Context, host: str, port: int, username: str, password: str, scheme: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["client"] = DFSClient(host, port, scheme=scheme)
    ctx.obj["client"].login(username, password)


def _run(ctx: click.Context, fn, *args):
    try:
        result = fn(*args)
        return result
    except Exception as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        raise click.ClickException(str(exc)) from exc


@cli.command()
@click.argument("local_file", type=click.Path(exists=True, dir_okay=False))
@click.argument("dfs_path")
@click.pass_context
def put(ctx, local_file, dfs_path):
    result = _run(ctx, ctx.obj["client"].upload, local_file, dfs_path)
    click.secho(f"Uploaded version {result['version']} ({result['size_bytes']} bytes)", fg="green")


@cli.command()
@click.argument("dfs_path")
@click.argument("local_file", type=click.Path(dir_okay=False))
@click.option("--version", type=click.IntRange(min=1), default=None)
@click.pass_context
def get(ctx, dfs_path, local_file, version):
    result = _run(ctx, ctx.obj["client"].download, dfs_path, local_file, version)
    click.secho(f"Downloaded {result['size_bytes']} bytes; SHA-256 {result['sha256']}", fg="green")


@cli.command(name="ls")
@click.argument("dfs_path", required=False, default="/")
@click.pass_context
def ls_command(ctx, dfs_path):
    entries = _run(ctx, ctx.obj["client"].ls, dfs_path)
    for entry in entries:
        click.echo(entry)


@cli.command()
@click.argument("dfs_path")
@click.pass_context
def mkdir(ctx, dfs_path):
    _run(ctx, ctx.obj["client"].mkdir, dfs_path)
    click.echo("Directory created")


@cli.command()
@click.argument("dfs_path")
@click.pass_context
def rmdir(ctx, dfs_path):
    _run(ctx, ctx.obj["client"].rmdir, dfs_path)
    click.echo("Directory removed")


@cli.command()
@click.argument("dfs_path")
@click.pass_context
def rm(ctx, dfs_path):
    _run(ctx, ctx.obj["client"].rm, dfs_path)
    click.echo("File removed")


@cli.command()
@click.argument("dfs_path")
@click.option("--version", type=click.IntRange(min=1), default=None)
@click.pass_context
def open(ctx, dfs_path, version):
    manifest = _run(ctx, ctx.obj["client"].open, dfs_path, version)
    click.echo(f"Opened {manifest['path']} version {manifest['version']} "
               f"({manifest['size_bytes']} bytes, {len(manifest['blocks'])} blocks)")


@cli.command()
@click.argument("dfs_path")
@click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True)
@click.option("--length", type=click.IntRange(min=0), default=None)
@click.option("--version", type=click.IntRange(min=1), default=None)
@click.option("--output", "output_path", type=click.Path(dir_okay=False), required=True)
@click.pass_context
def read(ctx, dfs_path, offset, length, version, output_path):
    data = _run(ctx, ctx.obj["client"].read, dfs_path, offset, length, version)
    with open(output_path, "wb") as stream:
        stream.write(data)
    click.echo(f"Read {len(data)} bytes to {output_path}")


@cli.command()
@click.argument("dfs_path")
@click.argument("local_patch", type=click.Path(exists=True, dir_okay=False))
@click.option("--offset", type=click.IntRange(min=0), required=True)
@click.pass_context
def write(ctx, dfs_path, local_patch, offset):
    with open(local_patch, "rb") as stream:
        data = stream.read()
    result = _run(ctx, ctx.obj["client"].write, dfs_path, offset, data)
    click.secho(f"Write committed as version {result['version']}", fg="green")


@cli.command()
@click.argument("dfs_path")
@click.option("--ttl", type=click.IntRange(min=5, max=300), default=30, show_default=True)
@click.pass_context
def lock(ctx, dfs_path, ttl):
    lock_id = _run(ctx, ctx.obj["client"].acquire_lock, dfs_path, ttl)
    click.echo(lock_id)


@cli.command()
@click.argument("lock_id")
@click.pass_context
def unlock(ctx, lock_id):
    _run(ctx, ctx.obj["client"].release_lock, lock_id)
    click.echo("Lock released")


@cli.command()
@click.pass_context
def pwd(ctx):
    click.echo(ctx.obj["client"].pwd())


@cli.command()
@click.pass_context
def shell(ctx):
    """Start an interactive session with persistent cd/pwd state."""
    client = ctx.obj["client"]
    click.echo("DFSha shell. Commands: ls, cd, pwd, mkdir, rmdir, rm, put, get, exit")
    while True:
        try:
            line = click.prompt(f"dfsha:{client.pwd()}", prompt_suffix=">", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            click.echo()
            return
        try:
            args = shlex.split(line)
            if not args:
                continue
            command, *values = args
            if command in {"exit", "quit"}:
                return
            if command == "pwd":
                click.echo(client.pwd())
            elif command == "cd" and len(values) == 1:
                click.echo(client.change_directory(values[0]))
            elif command == "ls" and len(values) <= 1:
                for entry in client.ls(values[0] if values else "."):
                    click.echo(entry)
            elif command == "mkdir" and len(values) == 1:
                client.mkdir(values[0])
            elif command == "rmdir" and len(values) == 1:
                client.rmdir(values[0])
            elif command == "rm" and len(values) == 1:
                client.rm(values[0])
            elif command == "put" and len(values) == 2:
                result = client.upload(values[0], values[1])
                click.echo(f"Committed version {result['version']}")
            elif command == "get" and len(values) == 2:
                client.download(values[0], values[1])
                click.echo("Download complete")
            else:
                click.echo("Usage: ls [path] | cd path | pwd | mkdir/rmdir/rm path | put local remote | get remote local | exit")
        except Exception as exc:
            click.secho(f"Error: {exc}", fg="red", err=True)


if __name__ == "__main__":
    cli()
