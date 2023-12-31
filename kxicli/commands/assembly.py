import copy
import json
import os
import random
import sys
import time
from tempfile import mkstemp
import requests
from tabulate import tabulate


import click
import pyk8s
import yaml
from click_aliases import ClickAliasedGroup

from kxicli import common
from kxicli import log
from kxicli.commands.common import arg
from kxicli.common import get_default_val as default_val
from kxicli.common import get_help_text as help_text
from kxicli.cli_group import ProfileAwareGroup, cli
from kxicli import options
from kxicli.options import namespace as options_namespace, assembly_backup_filepath, assembly_filepath, \
     hostname as options_hostname, \
     realm as options_realm
from kxicli.resources import auth

from kxicli.resources.auth import TokenType
from kxi.auth import CredentialStore
from kxi.controller.assembly import AssemblyApi as Assembly

API_GROUP = 'insights.kx.com'
API_VERSION = 'v1'
API_PLURAL = 'assemblies'
CONFIG_ANNOTATION = 'kubectl.kubernetes.io/last-applied-configuration'
ASM_LABEL_SELECTOR = 'insights.kx.com/queryEnvironment!=true'

local_arg_assembly_backup_filepath = assembly_backup_filepath.decorator(click_option_args=['-f', '--filepath'])


@cli.group(cls=ProfileAwareGroup)
def assembly():
    """Assembly interaction commands"""


def _format_assembly_status(assembly):
    """Format Kubernetes assembly status into CLI assembly status"""
    status = {}
    if 'status' in assembly and 'conditions' in assembly['status']:
        for d in assembly['status']['conditions']:
            cdata = {'status': d['status']}
            if 'message' in d:
                cdata['message'] = d['message']
            if 'reason' in d:
                cdata['reason'] = d['reason']

            status[d['type']] = cdata

    return status


def _assembly_status_k8s(namespace, name):
    """Get status of assembly via kubernetes API"""

    try:
        return pyk8s.cl.assemblies.read(name, namespace=namespace)
    except pyk8s.exceptions.NotFoundError:
        raise click.ClickException(f'Assembly {name} not found')
    except Exception as exception:
        raise click.ClickException(
            f'Exception when calling CustomObjectsApi->get_namespaced_custom_object: {exception}'
        ) from exception



def _assembly_status(namespace=None, name=None, hostname=None, realm=None, use_kubeconfig=False, print_status=False):
    """Get status of assembly"""

    assembly_ready = False

    if use_kubeconfig:
        namespace = options_namespace.prompt(namespace)
        res = _assembly_status_k8s(namespace, name)
        assembly_status = _format_assembly_status(res)
        if len(assembly_status) and 'AssemblyReady' in assembly_status:
            assembly_ready = assembly_status['AssemblyReady']['status'] == 'True'
    else:
        assembly = get_assembly_object(hostname, realm=realm)
        assembly_status = assembly.status(name=name)
        if len(assembly_status) and 'ready' in assembly_status:
            assembly_ready = assembly_status['ready']

    if print_status:
        if len(assembly_status) == 0:
            raise click.ClickException('Assembly not yet deployed')
        else:
            click.echo(json.dumps(assembly_status, indent=2))

    return assembly_ready


def _list_assemblies(hostname=None, realm=None, namespace=None, use_kubeconfig=False):
    """List assemblies"""

    if use_kubeconfig:
        namespace = options_namespace.prompt(namespace)
        res = get_assemblies_list(namespace)
        asm_list = format_assemblies_list_k8s(res)
    else:
        assembly = get_assembly_object(hostname, realm=realm)
        res = assembly.list()
        asm_list = _format_assemblies_list_kxic(res)

    print_2d_list(asm_list[0], asm_list[1])
    return True


def get_assemblies_list(namespace, label_selector=ASM_LABEL_SELECTOR, field_selector=None):
    """List assemblies via the kubernetes API"""
    return pyk8s.cl.assemblies.get(field_selector=field_selector, label_selector=label_selector, namespace=namespace)


def list_cluster_assemblies(field_selector=None, label_selector=None):
    """List assemblies via the kubernetes API"""
    return pyk8s.cl.assemblies.get(field_selector=field_selector, label_selector=label_selector, namespace=None)


def format_assemblies_list_k8s(assembly_list):
    """Extract assemblies returned from the kubernetes API"""
    asm_list = []
    for asm in assembly_list:
        if 'metadata' in asm and 'name' in asm['metadata']:
            asm_list.append((asm['metadata']['name'], asm['metadata']['namespace']))

    return (asm_list, ['ASSEMBLY NAME', 'NAMESPACE'])


def _format_assemblies_list_kxic(assembly_list):
    """Extract assemblies returned from the kxi-controller API"""
    asm_list = []
    for asm in assembly_list:
        if 'name' in asm:
            asm_list.append((asm['name'], asm['running'], asm['ready']))

    return (asm_list, ['ASSEMBLY NAME', 'RUNNING', 'READY'])


def print_2d_list(data, headers):
    """
    Prints a col-formatted 2d list
    """
    click.echo(tabulate([headers] + (data), tablefmt="plain", numalign="left", stralign="left"))

def backup_assemblies(namespace, filepath, force):
    """Get assemblies' definitions"""
    res = get_assemblies_list(namespace)
    backup = {"items": []}

    asm_list = []
    asm_backup_list = []

    for asm in res:
        if 'metadata' in asm and 'name' in asm['metadata']:
            asm_list.append(asm['metadata']['name'])
            if 'annotations' in asm['metadata'] and CONFIG_ANNOTATION in asm['metadata']['annotations']:
                asm_backup_list.append(asm['metadata']['name'])
                last_applied = json.loads(asm['metadata']['annotations'][CONFIG_ANNOTATION])
                backup['items'].append(last_applied)

    if len(asm_list) == 0:
        click.echo('No assemblies to back up')
        return None

    asm_exclude_list = [x for x in asm_list if x not in asm_backup_list]

    filepath = _backup_filepath(filepath, force)

    with open(filepath, 'w') as f:
        yaml.dump(backup, f)

    if len(asm_exclude_list) > 0:
        log.warn(f"Refusing to backup assemblies: {asm_exclude_list}. These assemblies are missing 'kubectl.kubernetes.io/last-applied-configuration' annotation. Please restart these assemblies manually.")

    click.echo(f'Persisted assembly definitions for {asm_backup_list} to {filepath}')

    return filepath


def _backup_filepath(filepath, force):
    if filepath:
        if not force and os.path.exists(filepath) and \
            not click.confirm(f'\n{filepath} file exists. Do you want to overwrite it with a new assembly backup file?'):
            filepath = click.prompt('Please enter the path to write the assembly backup file')
    else:
        _, filepath = mkstemp(f'-{common.DEFAULT_VALUES[common.key_assembly_backup_file]}')

    return filepath


def _read_assembly_file(filepath):
    if not os.path.exists(filepath):
        raise click.ClickException(f'File not found: {filepath}')

    with open(filepath) as f:
        try:
            body = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise click.ClickException(f'Invalid assembly file {filepath}')

    return body


def create_assemblies_from_file(filepath, hostname=None, realm=None, namespace=None, use_kubeconfig=False, wait=None):
    """Apply assemblies from file"""
    if not filepath:
        click.echo('No assemblies to restore')
        return []
    asm_list = _read_assembly_file(filepath)

    click.echo(f'Submitting assembly from {filepath}')
    created = []
    if 'items' in asm_list:
        for asm in asm_list['items']:
            click.echo(f"Submitting assembly {asm['metadata']['name']}")
            try_append(created, hostname, realm, namespace, asm, use_kubeconfig, wait)
    else:
        try_append(created, hostname, realm, namespace, asm_list, use_kubeconfig, wait)

    return created

def try_append(created = None, hostname=None, realm=None, namespace=None, asm=None, use_kubeconfig=False, wait=None):
    try:
        created.append(_create_assembly(hostname, realm, namespace, asm, use_kubeconfig, wait))
    except requests.exceptions.HTTPError as e:
        res = json.loads(e.response.text)
        click.echo(f"Error: {res['message']}. {res['detail']['message']}")
    except pyk8s.exceptions.ApiException as e:
        res = json.loads(e.body)
        click.echo(f"Error: {res['reason']}. {res['message']}")

    return created

def _add_last_applied_configuration_annotation(body):
    annotated_body = copy.deepcopy(body)

    # ensure annotations exist
    if 'annotations' not in annotated_body['metadata']:
        annotated_body['metadata']['annotations'] = {}

    annotated_body['metadata']['annotations'][CONFIG_ANNOTATION] = "\n" + json.dumps(annotated_body)

    return annotated_body

def _create_assembly_k8s(namespace, body):
    """Create an assembly via k8s api"""
    asm = pyk8s.ResourceItem.parse_obj(body)
    return asm.create_(namespace=namespace)


def _create_assembly(hostname, realm, namespace, body, use_kubeconfig, wait=None):
    """Create an assembly"""

    if 'resourceVersion' in body['metadata']:
        del body['metadata']['resourceVersion']
    body = _add_last_applied_configuration_annotation(body)

    if use_kubeconfig:
        namespace = options_namespace.prompt(namespace)
        _create_assembly_k8s(namespace, body)
    else:
        assembly = get_assembly_object(hostname, realm=realm)
        assembly.deploy(body)

    if wait:
        with click.progressbar(range(10), label='Waiting for assembly to enter "Ready" state') as bar:
            for n in bar:
                if _assembly_status(
                    hostname=hostname,
                    name=body['metadata']['name'],
                    realm=realm,
                    namespace=namespace,
                    use_kubeconfig=use_kubeconfig
                ):
                    break
                time.sleep((2 ** n) + (random.randint(0, 1000) / 1000))

    click.echo(f'Custom assembly resource {body["metadata"]["name"]} created!')
    return True

def _delete_assembly_k8s_api(namespace, name):
    try:
        pyk8s.cl.assemblies.delete(name, namespace=namespace)
    except pyk8s.exceptions.NotFoundError:
        click.echo(f'Ignoring teardown, {name} not found')
        return False
    except Exception as exception:
        click.echo(f'Exception when trying to delete Assembly({name}): {exception}\n')
        return False

    return True


def _delete_assembly(namespace=None, name=None, wait=None, force=False, hostname=None, realm=None, use_kubeconfig=False):
    """Deletes an assembly given its name"""
    click.echo(f'Tearing down assembly {name}')

    if not force and not click.confirm(f'Are you sure you want to teardown {name}'):
        click.echo(f'Not tearing down assembly {name}')
        return False
    if use_kubeconfig:
        namespace = options_namespace.prompt(namespace)
        asm_delete_success = _delete_assembly_k8s_api(namespace, name)
    else:
        assembly = get_assembly_object(hostname, realm=realm)
        asm_delete_success = assembly.teardown(name=name)

    if not asm_delete_success:
        return False

    if wait:
        return wait_for_assembly_teardown(namespace, name, hostname, realm, use_kubeconfig)

    return True

def wait_for_assembly_teardown(namespace, name, hostname, realm, use_kubeconfig):
    asm_running = True
    with click.progressbar(range(10), label='Waiting for assembly to be torn down') as bar:
        for n in bar:
            try:
                _assembly_status(namespace,
                    name,
                    hostname,
                    realm,
                    use_kubeconfig,
                )
            except click.ClickException as exception:
                if exception.message == f'Assembly {name} not found':
                    asm_running = False
                    break
            time.sleep((2 ** n) + (random.randint(0, 1000) / 1000))
    if asm_running:
        log.error('Assembly was not torn down in time, exiting')

    return not asm_running


def delete_running_assemblies(namespace, wait, force):
    """Deletes all assemblies running in a namespace"""
    asm_list = get_assemblies_list(namespace)
    deleted = []
    for asm in asm_list:
        if 'metadata' in asm and 'name' in asm['metadata']:
            deleted.append(
                _delete_assembly(namespace=namespace, name=asm['metadata']['name'], wait=wait, force=force, use_kubeconfig=True))

    return deleted


@assembly.command()
@arg.namespace()
@local_arg_assembly_backup_filepath()
@arg.force()
def backup(namespace, filepath, force):
    """Back up running assemblies to a file"""

    namespace = options_namespace.prompt(namespace)

    backup_assemblies(namespace, filepath, force)


@assembly.command(aliases=['create'])
@arg.hostname()
@arg.client_id()
@arg.client_secret()
@arg.realm()
@arg.namespace()
@arg.assembly_filepath()
@arg.assembly_wait()
@arg.use_kubeconfig()
def deploy(hostname, client_id, client_secret, realm, namespace, filepath, use_kubeconfig, wait):
    """Create an assembly given an assembly file"""
    filepath = assembly_filepath.prompt(filepath)
    host = options.get_hostname()
    create_assemblies_from_file(
        filepath,
        hostname=host,
        realm=realm,
        namespace=namespace,
        use_kubeconfig=use_kubeconfig,
        wait=wait
    )


@assembly.command()
@arg.namespace()
@arg.assembly_name()
@arg.assembly_wait()
@arg.hostname()
@arg.client_id()
@arg.client_secret()
@arg.realm()
@arg.use_kubeconfig()
def status(namespace, name, wait, hostname, client_id, client_secret, realm, use_kubeconfig):
    """Print status of the assembly"""
    host = options.get_hostname()
    namespace = options_namespace.prompt(namespace)

    if wait:
        with click.progressbar(range(10), label='Waiting for assembly to enter "Ready" state') as bar:
            for n in bar:
                if _assembly_status(namespace, name, host, realm, use_kubeconfig, print_status=True):
                    break
                time.sleep((2 ** n) + (random.randint(0, 1000) / 1000))
    else:
        _assembly_status(namespace, name, host, realm, use_kubeconfig, print_status=True)


@assembly.command()
@arg.hostname()
@arg.client_id()
@arg.client_secret()
@arg.realm()
@arg.namespace()
@arg.use_kubeconfig()
def list(hostname, realm, client_id, client_secret, namespace, use_kubeconfig):
    """List assemblies"""
    host = options.get_hostname()
    if _list_assemblies(host, realm, namespace, use_kubeconfig):
        sys.exit(0)
    else:
        sys.exit(1)


@assembly.command(aliases=['delete'])
@arg.namespace()
@arg.assembly_name()
@arg.assembly_wait()
@arg.force()
@arg.hostname()
@arg.client_id()
@arg.client_secret()
@arg.realm()
@arg.use_kubeconfig()
def teardown(namespace, name, wait, force, hostname, client_id, client_secret, realm, use_kubeconfig):
    """Tears down an assembly given its name"""
    host = options.get_hostname()
    _delete_assembly(namespace, name, wait, force, host, realm, use_kubeconfig)


def get_preferred_api_version(group_name):
    try:
        return pyk8s.cl.assemblies.api_version
    except Exception:
        raise click.ClickException(f'Could not find preferred API version for group {group_name}')



def get_assembly_object(hostname, realm):
    store = CredentialStore(name = options.get_profile(), file_path= common.token_cache_file, 
                            file_format= common.token_cache_format)

    grant_type = store.get('grant_type', default=TokenType.SERVICEACCOUNT)
    client_id = options.get_serviceaccount_id()
    
    if grant_type == TokenType.USER:
        client_id = store.get('client_id')

    return  Assembly(hostname, realm=realm, client_id=client_id, grant_type=grant_type,
                        client_secret = options.get_serviceaccount_secret(), cache=store)