from __future__ import annotations

import click
import json
import sys
import uuid

from pydantic.json import pydantic_encoder
from typing import List

from kxi.entitlement import EntitlementService, EntityType
from kxicli import options, common, log
from kxicli.commands.common import arg
from kxicli.cli_group import ProfileAwareGroup, cli
from kxi.auth import CredentialStore
from kxicli.resources.auth import TokenType

api_client_params = arg.combine_decorators(
    arg.hostname(),
    arg.realm(),
    arg.timeout()
)


# ** Internal functions ** #


def _ensure_host(hostname: str | None) -> str:
    """Ensure that hostname is set and conforms to the correct format

    Args:
        hostname: String that may or may not be None and have http prefix
    """
    return f'https://{common.sanitize_hostname(options.hostname.prompt(hostname, silent=True))}'


def _parse_groups(groups: str) -> List[uuid.UUID]:
    """Split string of groups into list of UUIDs

    Args:
        groups: String of UUIDs separated by commas
    """
    ids = []

    if groups is None:
        return ids

    for group in groups.split(','):
        try:
            group_id = uuid.UUID(group.strip())
            ids.append(group_id)
        except ValueError:
            raise click.ClickException(f'{group.strip()} is not a valid UUID')

    return ids


def _exception_handler(exception_type, exception, traceback):
    """Squash tracebacks for the CLI unless the --debug flag is set
    """
    if not log.GLOBAL_DEBUG_LOG:
        click.echo(f"{exception_type.__name__}: {exception}")
    else:
        sys.__excepthook__(exception_type, exception, traceback)


sys.excepthook = _exception_handler


# ** Click commands ** #


@cli.group(cls=ProfileAwareGroup)
def entitlement():
    """Insights entitlements commands"""


@entitlement.command()
@api_client_params
def list(
    hostname,
    realm,
    timeout
):
    """List entitlements"""
    e = get_credentialstore_object(hostname , realm , timeout)

    click.echo(json.dumps(e.list(), default=pydantic_encoder))


@entitlement.command()
@api_client_params
def actors(
    hostname,
    realm,
    timeout
):
    """List actors

    Actors are the list of groups that can be assigned to an entity.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    click.echo(json.dumps(e.actors(), default=pydantic_encoder))


@entitlement.command()
@click.argument("id", type=click.UUID)
@click.argument("name")
@click.argument("type", type=EntityType)
@click.option("--owner", type=click.UUID, help="User ID of the owner")
@click.option("--groups", help="Comma separated list of group IDs")
@api_client_params
def create(
    id,
    name,
    type,
    owner,
    groups,
    hostname,
    realm,
    timeout
):
    """Create an entitlement

    ID is the ID of the entity being entitled.
    NAME is the name of the entity being entitled.
    TYPE is the type of the entity being entitled

    Currently the only supported entity type is 'assembly'

    --owner and --groups are optional, if not provided these will be defaulted to 00000000-0000-0000-0000-000000000000 and [] respectively.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    click.echo(e.create(id, name, type, owner, _parse_groups(groups)))


@entitlement.command()
@click.argument("id", type=click.UUID)
@click.option("--name", help="Name of the entity")
@click.option("--owner", type=click.UUID, help="User ID of the owner")
@click.option("--groups", help="Comma separated list of group IDs")
@api_client_params
def update(
    id,
    name,
    owner,
    groups,
    hostname,
    realm,
    timeout
):
    """Update an entitlement

    This updates the name, owner and groups of an entitlement based on the options passed.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    click.echo(e.update(id, name, owner=owner, groups=_parse_groups(groups)))


@entitlement.command()
@click.argument("id", type=click.UUID)
@api_client_params
@arg.force()
def delete(
    id,
    hostname,
    realm,
    timeout,
    force
):
    """Delete an entitlement

    This deletes an entitlement pending user confirmation.
    Use --force to skip user confirmation.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    if force or click.confirm('Are you sure you want to delete this entitlement?') :
        click.echo(json.dumps(e.delete(id), default=pydantic_encoder))


@entitlement.command()
@click.argument("id", type=click.UUID)
@api_client_params
def get(
    id,
    hostname,
    realm,
    timeout
):
    """Get an entitlement"""
    e = get_credentialstore_object(hostname , realm , timeout)

    click.echo(json.dumps(e.get(id), default=pydantic_encoder))


@entitlement.command()
@click.argument("id", type=click.UUID)
@click.argument("groups")
@api_client_params
def add_groups(
    id,
    groups,
    hostname,
    realm,
    timeout
):
    """Add groups to an entitlement

    ID is the id of the the entity to add the groups to.
    GROUPS is a comma separated list of group IDs to add.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    # get entity
    entity = e.get(id)

    # append groups
    updated_groups = []
    groups_to_add = _parse_groups(groups)

    if entity.groups is not None:
        updated_groups += entity.groups
    updated_groups.extend(x for x in groups_to_add if x not in updated_groups)

    # update entity
    click.echo(json.dumps(e.update(id, groups=updated_groups), default=pydantic_encoder))


@entitlement.command()
@click.argument("id", type=click.UUID)
@click.argument("groups")
@api_client_params
def rm_groups(
    id,
    groups,
    hostname,
    realm,
    timeout
):
    """Remove groups from an entitlement

    ID is the id of the the entity to remove the groups from.
    GROUPS is a comma separated list of group IDs to remove.
    """
    e = get_credentialstore_object(hostname , realm , timeout)

    # get entity
    entity = e.get(id)

    # remove groups
    existing_groups = entity.groups
    groups_to_remove = _parse_groups(groups)
    new_groups = [g for g in existing_groups if g not in groups_to_remove]

    # update entity
    click.echo(json.dumps(e.update(id, groups=new_groups), default=pydantic_encoder))

def get_credentialstore_object(hostname, realm, timeout):
    store = CredentialStore(name = options.get_profile(), file_path= common.token_cache_file, 
                            file_format= common.token_cache_format)
    
    grant_type = store.get('grant_type', default=TokenType.SERVICEACCOUNT)
    client_id = options.get_serviceaccount_id()
    
    if grant_type == TokenType.USER:
        client_id = store.get('client_id')

    return EntitlementService(
        host=_ensure_host(hostname),
        realm=realm,
        timeout=timeout,
        client_id=client_id, 
        grant_type=grant_type,
        client_secret = options.get_serviceaccount_secret(), 
        cache=store
    )