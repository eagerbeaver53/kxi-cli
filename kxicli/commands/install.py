import base64
import datetime
import json
import os
import random
import string
import subprocess
import sys
from pathlib import Path

import click
import kubernetes as k8s
import yaml
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, asymmetric, hashes

from kxicli import common
from kxicli import log
from kxicli.commands import assembly
from kxicli.commands.common import arg_force, arg_filepath, arg_version, arg_operator_version, \
    arg_release, arg_namespace, arg_assembly_backup_filepath
from kxicli.common import get_default_val as default_val
from kxicli.common import get_help_text as help_text

docker_config_file_path = str(Path.home() / '.docker' / 'config.json')
operator_namespace = 'kxi-operator'

SECRET_TYPE_TLS = 'kubernetes.io/tls'
SECRET_TYPE_DOCKERCONFIG_JSON = 'kubernetes.io/dockerconfigjson'
SECRET_TYPE_OPAQUE = 'Opaque'

TLS_CRT = 'tls.crt'
TLS_KEY = 'tls.key'

# Basic validation for secrets checking secret type and data
# Format is [expected_secret_type, required_secret_keys]
# required_secret_keys must be a tuple, one-item tuples have a trailing comma
SECRET_VALIDATION = {
    'keycloak': (SECRET_TYPE_OPAQUE, ('admin-password', 'management-password')),
    'postgresql': (SECRET_TYPE_OPAQUE, ('postgresql-postgres-password', 'postgresql-password')),
    'license': (SECRET_TYPE_OPAQUE, ('license',)),
    'image_pull': (SECRET_TYPE_DOCKERCONFIG_JSON, ('.dockerconfigjson',)),
    'ingress_cert': (SECRET_TYPE_TLS, (TLS_CRT, TLS_KEY)),
    'client_cert': (SECRET_TYPE_TLS, (TLS_CRT, TLS_KEY))
}


@click.group()
def install():
    """Insights installation commands"""


@install.command()
@arg_namespace()
@click.option('--chart-repo-name', default=lambda: default_val('chart.repo.name'), help=help_text('chart.repo.name'))
@click.option('--license-secret', default=lambda: default_val('license.secret'), help=help_text('license.secret'))
@click.option('--license-as-env-var', default=False, help=help_text('license.envVar'))
@click.option('--client-cert-secret', default=lambda: default_val('client.cert.secret'),
              help=help_text('client.cert.secret'))
@click.option('--image-repo', default=lambda: default_val('image.repository'), help=help_text('image.repository'))
@click.option('--image-pull-secret', default=lambda: default_val('image.pullSecret'),
              help=help_text('image.pullSecret'))
@click.option('--gui-client-secret', default=lambda: default_val('guiClientSecret'), help=help_text('guiClientSecret'))
@click.option('--operator-client-secret', default=lambda: default_val('operatorClientSecret'),
              help=help_text('operatorClientSecret'))
@click.option('--keycloak-secret', default=lambda: default_val('keycloak.secret'), help=help_text('keycloak.secret'))
@click.option('--keycloak-postgresql-secret', default=lambda: default_val('keycloak.postgresqlSecret'),
              help=help_text('keycloak.postgresqlSecret'))
@click.option('--keycloak-auth-url', help=help_text('keycloak.authURL'))
@click.option('--ingress-host', help=help_text('ingress.host'))
@click.option('--ingress-cert-secret', default=lambda: default_val('ingress.cert.secret'),
              help=help_text('ingress.cert.secret'))
@click.option('--output-file', default=lambda: default_val('install.outputFile'), help=help_text('install.outputFile'))
@click.option('--install-config-secret', default=lambda: default_val('install.configSecret'),
              help=help_text('install.configSecret'))
def setup(namespace, chart_repo_name, license_secret, license_as_env_var, client_cert_secret, image_repo,
          image_pull_secret, gui_client_secret, operator_client_secret,
          keycloak_secret, keycloak_postgresql_secret, keycloak_auth_url, ingress_host, ingress_cert_secret,
          output_file, install_config_secret):
    """Perform necessary setup steps to install Insights"""

    click.secho('KX Insights Install Setup', bold=True)

    active_context, namespace = common.get_namespace(namespace)
    create_namespace(namespace)
    click.echo(f'\nRunning in namespace {namespace} on the cluster {active_context["context"]["cluster"]}')

    if '--ingress-host' not in sys.argv:
        ingress_host = sanitize_ingress_host(click.prompt('\nPlease enter the hostname for the installation'))

    if '--chart-repo-name' not in sys.argv:
        click.secho('\nChart details', bold=True)
        chart_repo_name = click.prompt('Please enter a name for the chart repository to set locally',
                                       default=default_val('chart.repo.name'))
        chart_repo_url = click.prompt('Please enter the chart repository URL to pull charts from',
                                      default=default_val('chart.repo.url'))
        username = click.prompt('Please enter the username for the chart repository')
        password = click.prompt('Please enter the password for the chart repository (input hidden)', hide_input=True)
        helm_add_repo(chart_repo_name, chart_repo_url, username, password)

    if '--license-secret' not in sys.argv:
        click.secho('\nLicense details', bold=True)
        license_secret, license_on_demand = prompt_for_license(namespace, license_secret, license_as_env_var)

    if not ('--image-repo' in sys.argv and '--image-pull-secret' in sys.argv):
        click.secho('\nImage repository', bold=True)
        image_repo, image_pull_secret = prompt_for_image_details(namespace, image_repo, image_pull_secret)

    if '--client-cert-secret' not in sys.argv:
        click.secho('\nClient certificate issuer', bold=True)
        client_cert_secret = prompt_for_client_cert(namespace, client_cert_secret)

    click.secho('\nKeycloak', bold=True)
    if deploy_keycloak() and not ('--keycloak-secret' in sys.argv and '--keycloak-postgresql-secret' in sys.argv):
        keycloak_secret, keycloak_postgresql_secret = prompt_for_keycloak(namespace, keycloak_secret,
                                                                          keycloak_postgresql_secret)

    if not gui_client_secret:
        gui_client_secret = prompt_for_client_secret('gui')
        common.config.append_config(profile=common.config.config.default_section, name='guiClientSecret',
                                    value=gui_client_secret)

    if not operator_client_secret:
        operator_client_secret = prompt_for_client_secret('operator')
        common.config.append_config(profile=common.config.config.default_section, name='operatorClientSecret',
                                    value=operator_client_secret)

    if 'ingress-cert-secret' not in sys.argv:
        click.secho('\nIngress', bold=True)
        ingress_self_managed, ingress_cert_secret = prompt_for_ingress_cert(namespace, ingress_cert_secret)

    # These keys must all exist, conditionally defined
    # keys like the self-managed ingress cert are handled afterwards
    install_file = {
        'global': {
            'ingress': {
                'host': ingress_host
            },
            'license': {
                'secretName': license_secret
            },
            'caIssuer': {
                'name': client_cert_secret,
                'secretName': client_cert_secret
            },
            'image': {
                'repository': image_repo
            },
            'imagePullSecrets': [
                {
                    'name': image_pull_secret
                }
            ],
            'keycloak': {
                'guiClientSecret': gui_client_secret,
                'operatorClientSecret': operator_client_secret
            }
        }
    }

    if deploy_keycloak():
        install_file['keycloak'] = {
            'auth': {
                'existingSecret': keycloak_secret
            },
            'postgresql': {
                'auth': {
                    'existingSecret': keycloak_postgresql_secret
                },
                'existingSecret': keycloak_postgresql_secret
            }
        }
    else:
        install_file['global']['keycloak']['authURL'] = sanitize_auth_url(keycloak_auth_url)
        install_file['keycloak'] = {'enabled': False}
        install_file['keycloak-config-cli'] = {'enabled': True}

    if ingress_self_managed:
        install_file['global']['ingress']['certmanager'] = False
        install_file['global']['ingress']['tlsSecret'] = ingress_cert_secret

    if license_as_env_var:
        install_file['global']['license']['asFile'] = False

    if license_on_demand:
        install_file['global']['license']['onDemand'] = True
        install_file['kxi-acc-svc'] = {'enabled': False}

    if os.path.exists(output_file):
        if not click.confirm(f'\n{output_file} file exists. Do you want to overwrite it with a new values file?'):
            output_file = click.prompt('Please enter the path to write the values file for the install')

    with open(output_file, 'w') as f:
        yaml.dump(install_file, f)

    create_install_config_secret(namespace, install_config_secret, install_file)

    click.secho('\nKX Insights installation setup complete', bold=True)
    click.echo(f'\nHelm values file for installation saved in {output_file}\n')

    return output_file, chart_repo_name


@install.command()
@arg_namespace()
@arg_filepath()
@arg_release()
@click.option('--chart-repo-name', default=lambda: default_val('chart.repo.name'), help=help_text('chart.repo.name'))
@arg_version()
@arg_operator_version()
@click.option('--image-pull-secret', default=None, help=help_text('image.pullSecret'))
@click.option('--license-secret', default=None, help=help_text('license.secret'))
@click.option('--install-config-secret', default=None, help=help_text('install.configSecret'))
@arg_force()
@click.pass_context
def run(ctx, namespace, filepath, release, chart_repo_name, version, operator_version, image_pull_secret,
        license_secret, install_config_secret, force):
    """Install KX Insights with a values file"""

    # Run setup prompts if necessary
    if filepath is None and install_config_secret is None:
        click.echo('No values file provided, invoking "kxi install setup"\n')
        filepath, chart_repo_name = ctx.invoke(setup)

    _, namespace = common.get_namespace(namespace)

    values_secret = get_install_values(namespace=namespace, install_config_secret=install_config_secret)
    image_pull_secret, license_secret = get_image_and_license_secret_from_values(values_secret, filepath,
                                                                                 image_pull_secret, license_secret)

    insights_installed_charts = get_installed_charts(release, namespace)
    if len(insights_installed_charts) > 0:
        if click.confirm(f'KX Insights is already installed with version {insights_installed_charts[0]["chart"]}. Would you like to upgrade to version {version}?'):
            return perform_upgrade(namespace, release, chart_repo_name, default_val('assembly.backup.file'), version,
                                   operator_version, image_pull_secret, license_secret, install_config_secret,
                                   filepath, force)
        else:
            sys.exit(0)

    install_operator_and_release(release=release, namespace=namespace, version=version,
                                 operator_version=operator_version, values_file=filepath, values_secret=values_secret,
                                 image_pull_secret=image_pull_secret, license_secret=license_secret,
                                 chart_repo_name=chart_repo_name, force=force, prompt_to_install_operator=False)

@install.command()
@arg_namespace()
@arg_release()
@click.option('--chart-repo-name', default=lambda: default_val('chart.repo.name'), help=help_text('chart.repo.name'))
@arg_assembly_backup_filepath()
@arg_version()
@arg_operator_version()
@click.option('--image-pull-secret', default=None, help=help_text('image.pullSecret'))
@click.option('--license-secret', default=None, help=help_text('license.secret'))
@click.option('--install-config-secret', default=None, help=help_text('install.configSecret'))
@arg_filepath()
@arg_force()
def upgrade(namespace, release, chart_repo_name, assembly_backup_filepath, version, operator_version, image_pull_secret,
            license_secret, install_config_secret, filepath, force):
    perform_upgrade(namespace, release, chart_repo_name, assembly_backup_filepath, version, operator_version, image_pull_secret,
                    license_secret, install_config_secret, filepath, force)

def perform_upgrade(namespace, release, chart_repo_name, assembly_backup_filepath, version, operator_version, image_pull_secret,
                    license_secret, install_config_secret, filepath, force):
    """Upgrade KX Insights"""
    _, namespace = common.get_namespace(namespace)

    upgraded = False
    click.secho('Upgrading KX Insights', bold=True)

    # Read install values
    if filepath is None and install_config_secret is None:
        log.error('At least one of --install-config-secret and --filepath options must be provided')
        sys.exit(1)
    values_secret = get_install_values(namespace=namespace, install_config_secret=install_config_secret)
    image_pull_secret, license_secret = get_image_and_license_secret_from_values(values_secret, filepath,
                                                                                 image_pull_secret, license_secret)

    if not insights_installed(release, namespace):
        click.echo('KX Insights is not deployed. Skipping to install')
        install_operator_and_release(release=release, namespace=namespace, version=version,
                                     operator_version=operator_version, values_file=filepath,
                                     values_secret=values_secret, image_pull_secret=image_pull_secret,
                                     license_secret=license_secret, chart_repo_name=chart_repo_name,
                                     force=force, prompt_to_install_operator=False)
        click.secho(f'\nUpgrade to version {version} complete', bold=True)
        sys.exit(0)

    click.secho('\nBacking up assemblies', bold=True)
    assembly_backup_filepath = assembly._backup_assemblies(namespace, assembly_backup_filepath, force)

    click.secho('\nTearing down assemblies', bold=True)
    click.secho('Assembly data will be persisted and state will be recovered post-upgrade')
    deleted = assembly._delete_running_assemblies(namespace=namespace, wait=True, force=force)

    if all(deleted):
        click.secho('\nUpgrading insights and operator', bold=True)
        upgraded = install_operator_and_release(release=release, namespace=namespace, version=version,
                                                operator_version=operator_version, values_file=filepath,
                                                values_secret=values_secret, image_pull_secret=image_pull_secret,
                                                license_secret=license_secret, chart_repo_name=chart_repo_name,
                                                force=force, prompt_to_install_operator=True)

    click.secho('\nReapplying assemblies', bold=True)
    assembly._create_assemblies_from_file(namespace=namespace, filepath=assembly_backup_filepath)

    if upgraded:
        click.secho(f'\nUpgrade to version {version} complete', bold=True)


@install.command()
@arg_release()
@arg_namespace()
@arg_force()
def delete(release, namespace, force):
    """Uninstall KX Insights"""
    delete_release_operator_and_crds(release=release, namespace=namespace, force=force)


@install.command()
@click.option('--chart-repo-name', default=lambda: default_val('chart.repo.name'), help=help_text('chart.repo.name'))
def list_versions(chart_repo_name):
    """
    List available versions of KX Insights
    """
    helm_list_versions(chart_repo_name)


@install.command()
@arg_namespace()
@click.option('--install-config-secret', default=lambda: default_val('install.configSecret'),
              help=help_text('install.configSecret'))
def get_values(namespace, install_config_secret):
    """
    Display the kxi-install-config secret used for storing installation values
    """
    click.echo(get_install_config_secret(namespace=namespace, install_config_secret=install_config_secret))


def get_install_config_secret(namespace, install_config_secret):
    """
    Return the kxi-install-config secret used for storing installation values
    """
    values_secret = read_secret(namespace=namespace, name=install_config_secret)
    if values_secret:
        values_secret = base64.b64decode(values_secret.data['values.yaml']).decode('ascii')
    else:
        log.error(f'Cannot find values secret {install_config_secret}')

    return values_secret


def get_operator_version(chart_repo_name, insights_version, operator_version):
    """Determine operator version to use. Retrieve the most recent operator minor version matching the insights version"""
    if operator_version is None:
        insights_version_parsed = insights_version.split(".")
        insights_version_minor = insights_version_parsed[0] + "." + insights_version_parsed[1]
        ops_from_helm = subprocess.run(
            ['helm', 'search', 'repo', f'{chart_repo_name}/kxi-operator', '--version', f'{insights_version_minor}',
             '--output', 'json'], check=True, capture_output=True, text=True)
        ops_from_helm = json.loads(ops_from_helm.stdout)
        if len(ops_from_helm):
            operator_version = ops_from_helm[0]['version']
        else:
            log.error(f'Cannot find operator version matching insights minor version {insights_version_minor}')
            sys.exit(1)
    return operator_version


def sanitize_ingress_host(raw_string):
    """Sanitize a host name to allow it to be used"""
    return raw_string.replace('http://', '').replace('https://', '')


def sanitize_auth_url(raw_string):
    """Sanitize a Keycloak auth url to allow it to be used"""
    trimmed = raw_string.strip()

    if trimmed.startswith('https://'):
        click.echo('Replacing https:// with http:// in --keycloak-auth-url')
        trimmed = f"http://{trimmed.replace('https://', '')}"

    if not trimmed.startswith('http://'):
        trimmed = f'http://{trimmed}'

    if not trimmed.endswith('/'):
        trimmed = f'{trimmed}/'

    return trimmed


def prompt_for_license(namespace, license_secret, license_as_env_var):
    """Prompt for an existing license or create on if it doesn't exist"""
    license_on_demand = False
    if click.confirm('Do you have an existing license secret'):
        license_secret = prompt_and_validate_existing_secret(namespace, 'license')
    else:
        path_to_lic = click.prompt('Please enter the path to your kdb license')
        if os.path.basename(path_to_lic) == 'kc.lic':
            license_on_demand = True
        create_license_secret(namespace, license_secret, path_to_lic, license_as_env_var)

    return license_secret, license_on_demand


def prompt_for_client_cert(namespace, client_cert_secret):
    """Prompt for an existing client cert secret or create one if it doesn't exist"""
    if click.confirm('Do you have an existing client certificate issuer'):
        client_cert_secret = prompt_and_validate_existing_secret(namespace, 'client_cert')
    else:
        key = gen_private_key()
        cert = gen_cert(key)
        create_tls_secret(namespace, client_cert_secret, cert, key)

    return client_cert_secret


def prompt_for_image_details(namespace, image_repo, image_pull_secret):
    """Prompt for an existing image pull secret or create on if it doesn't exist"""
    image_repo = click.prompt('Please enter the image repository to pull images from', default=image_repo)

    if click.confirm(f'Do you have an existing image pull secret for {image_repo}'):
        image_pull_secret = prompt_and_validate_existing_secret(namespace, 'image_pull')
        return image_repo, image_pull_secret

    existing_config = check_existing_docker_config(image_repo, docker_config_file_path)

    if existing_config:
        # parse the user from the existing config which is a base64 encoded string of "username:password"
        user = base64.b64decode(existing_config['auth']).decode('ascii').split(':')[0]
        if click.confirm(
                f'Credentials {user}@{image_repo} exist in {docker_config_file_path}, do you want to use these'):
            docker_config = {
                'auths': {
                    image_repo: existing_config
                }
            }
            create_docker_config_secret(namespace, image_pull_secret, docker_config)
            return image_repo, image_pull_secret

    user = click.prompt(f'Please enter the username for {image_repo}')
    password = click.prompt(f'Please enter the password for {user} (input hidden)', hide_input=True)
    docker_config = create_docker_config(image_repo, user, password)
    create_docker_config_secret(namespace, image_pull_secret, docker_config)

    return image_repo, image_pull_secret


def prompt_for_keycloak(namespace, keycloak_secret, postgresql_secret):
    """Prompt for existing Keycloak secrets or create them if they don't exist"""

    if click.confirm('Do you have an existing keycloak secret'):
        keycloak_secret = prompt_and_validate_existing_secret(namespace, 'keycloak')
    else:
        admin_password = click.prompt('Please enter the Keycloak Admin password (input hidden)', hide_input=True)
        management_password = click.prompt('Please enter the Keycloak WildFly Management password (input hidden)',
                                           hide_input=True)
        data = {
            'admin-password': base64.b64encode(admin_password.encode()).decode('ascii'),
            'management-password': base64.b64encode(management_password.encode()).decode('ascii')
        }
        create_secret(namespace, keycloak_secret, 'Opaque', data=data)

    if click.confirm('Do you have an existing keycloak postgresql secret'):
        postgresql_secret = prompt_and_validate_existing_secret(namespace, 'postgresql')
    else:
        postgresql_postgres_password = click.prompt('Please enter the Postgresql postgres password (input hidden)',
                                                    hide_input=True)
        postgresql_password = click.prompt('Please enter the Postgresql user password (input hidden)', hide_input=True)

        data = {
            'postgresql-postgres-password': base64.b64encode(postgresql_postgres_password.encode()).decode('ascii'),
            'postgres-password': base64.b64encode(postgresql_postgres_password.encode()).decode('ascii'),
            'postgresql-password': base64.b64encode(postgresql_password.encode()).decode('ascii'),
            'password': base64.b64encode(postgresql_password.encode()).decode('ascii')
        }

        create_secret(namespace, postgresql_secret, 'Opaque', data=data)

    return keycloak_secret, postgresql_secret


def prompt_for_ingress_cert(namespace, ingress_cert_secret):
    if click.confirm('Do you want to provide a self-managed cert for the ingress'):
        ingress_self_managed = True
        if click.confirm('Do you have an existing secret containing the cert for the ingress'):
            ingress_cert_secret = prompt_and_validate_existing_secret(namespace, 'ingress_cert')
        else:
            path_to_cert = click.prompt('Please enter the path to your TLS certificate')
            with open(path_to_cert, 'r') as cert_file:
                cert_data = cert_file.read()
                cert = x509.load_pem_x509_certificate(cert_data.encode(), backend=default_backend())

            path_to_key = click.prompt('Please enter the path to your TLS private key')
            with open(path_to_key, 'r') as key_file:
                key_data = key_file.read()
                key = serialization.load_pem_private_key(key_data.encode(), password=None, backend=default_backend())

            create_tls_secret(namespace, ingress_cert_secret, cert, key)
    else:
        ingress_self_managed = False

    return ingress_self_managed, ingress_cert_secret


def create_docker_config(image_repo, user, password):
    """Output the .dockerconfigjson format given a repo, username and password"""
    config = {
        'auths': {
            image_repo: {
                'username': user,
                'password': password,
                'auth': base64.b64encode(f'{user}:{password}'.encode()).decode('ascii')
            }
        }
    }

    return config


def prompt_for_existing_secret():
    return click.prompt('Please enter the name of the existing secret')


def prompt_and_validate_existing_secret(namespace, secret_use):
    secret_name = prompt_for_existing_secret()
    if secret_use not in SECRET_VALIDATION:
        # if no validation exists, continue without validation
        log.debug(f'Could not find validation logic to validate the {secret_use} secret')
        return secret_name

    expected_type, required_keys = SECRET_VALIDATION[secret_use]
    if not validate_secret(namespace, secret_name, expected_type, required_keys)[0]:
        sys.exit(1)

    return secret_name


def check_existing_docker_config(image_repo, file_path):
    """Check local .docker/config.json for repo credentials"""
    log.debug(f'Checking {file_path} for existing credentials for the repository {image_repo}')
    try:
        with open(file_path, 'r') as f:
            config = json.loads(f.read())
        if 'auths' in config and image_repo in config['auths']:
            return config['auths'][image_repo]
    except FileNotFoundError:
        pass

    return None


def create_license_secret(namespace, name, filepath, asEnv=False):
    """Create a KX license secret in a given namespace"""

    with open(filepath, 'rb') as license_file:
        encoded_license = base64.b64encode(license_file.read())

    license_data = {
        'license': encoded_license.decode('ascii')
    }

    if asEnv:
        string_data = license_data
        data = None
    else:
        string_data = None
        data = license_data

    return create_secret(
        namespace=namespace,
        name=name,
        secret_type='Opaque',
        string_data=string_data,
        data=data
    )


def create_docker_config_secret(namespace, name, docker_config):
    """Create a KX a Docker config secret in a given namespace"""
    docker_config = json.dumps(docker_config).encode()
    data = {
        '.dockerconfigjson': base64.b64encode(docker_config).decode('ascii')
    }

    return create_secret(
        namespace=namespace,
        name=name,
        secret_type='kubernetes.io/dockerconfigjson',
        data=data
    )


def create_tls_secret(namespace, name, cert, key):
    """Create a TLS secret in a given namespace from a cert and private key"""

    # the private key must be unencrypted for a k8s secret
    key_string = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    cert_string = cert.public_bytes(serialization.Encoding.PEM)

    data = {
        TLS_KEY: base64.b64encode(key_string).decode('ascii'),
        TLS_CRT: base64.b64encode(cert_string).decode('ascii')
    }

    return create_secret(
        namespace,
        name=name,
        secret_type='kubernetes.io/tls',
        data=data
    )


def build_install_secret(data):
    return {'values.yaml': base64.b64encode(yaml.dump(data).encode()).decode('ascii')}


def create_install_config_secret(namespace, name, data):
    """Create a secret to store install values in a given namespace"""

    install_secret = build_install_secret(data)

    values_secret = read_secret(namespace=namespace, name=name)

    if values_secret:
        if click.confirm(f'Values file secret {name} already exists. Do you want to overwrite it?'):
            values_secret = patch_secret(namespace, name, 'Opaque', data=install_secret)
    else:
        log.debug(f'Secret {name} does not exist. Creating new secret.')
        values_secret = create_secret(namespace, name, 'Opaque', data=install_secret)

    return values_secret


def read_secret(namespace, name):
    common.load_kube_config()

    try:
        secret = k8s.client.CoreV1Api().read_namespaced_secret(namespace=namespace, name=name)
    except k8s.client.rest.ApiException as exception:
        # 404 is returned when this secret doesn't already exist.
        if exception.status == 404:
            return None
    else:
        return secret


def create_secret(namespace, name, secret_type, data=None, string_data=None):
    """Helper function to create a Kubernetes secret"""
    log.debug(f'Creating secret called {name} with type {secret_type} in namespace {namespace}')

    secret = get_secret_body(name, secret_type, data, string_data)
    common.load_kube_config()
    try:
        k8s.client.CoreV1Api().create_namespaced_secret(namespace, body=secret)
    except k8s.client.rest.ApiException as exception:
        log.error(f'Exception when trying to create secret {exception}')
        sys.exit(1)

    click.echo(f'Secret {name} successfully created')


def patch_secret(namespace, name, secret_type, data=None, string_data=None):
    """Helper function to update a Kubernetes secret"""
    log.debug(f'Updating secret {name} in namespace {namespace}')

    secret = get_secret_body(name, secret_type, data, string_data)
    common.load_kube_config()
    try:
        patched_secret = k8s.client.CoreV1Api().patch_namespaced_secret(name, namespace, body=secret)
    except k8s.client.rest.ApiException as exception:
        log.error(f'Exception when trying to update secret {exception}')
        sys.exit(1)

    click.echo(f'Secret {name} successfully updated')
    return patched_secret


def get_secret_body(name, secret_type, data=None, string_data=None):
    """Create the body for a request to create_namespaced_secret"""
    secret = k8s.client.V1Secret()
    secret.metadata = k8s.client.V1ObjectMeta(name=name)
    secret.type = secret_type

    if data:
        secret.data = data
    if string_data:
        secret.string_data = string_data

    return secret


def validate_secret(namespace, name, secret_type, data_keys):
    """Validates that specific keys exist in the data field of a secret and that the secret has the expected type

    :param str namespace: Namespace to search for the secret
    :param str name: Name of the secret
    :param str secret_type: Expected type of the secret
    :param tuple data_keys: Required keys in the secret
    :rtype: bool
    """
    secret = read_secret(namespace, name)
    if secret is None:
        log.error(f'Secret {name} does not exist in the namespace {namespace}')
        sys.exit(1)

    missing_data_keys = get_missing_keys(secret.data, data_keys)

    is_valid = True
    if secret.type != secret_type:
        log.error(f'Secret {name} is of type {secret.type} when it should {secret_type}')
        is_valid = False

    if missing_data_keys:
        log.error(f'Secret {name} is missing required data keys {missing_data_keys}')
        is_valid = False

    return (is_valid, missing_data_keys)


def get_missing_keys(dictionary, keys):
    """Returns keys from 'keys' that are missing from 'dictionary'

    :param dictionary: Dictionary to search for keys in
    :type dictionary: dict or None
    :param tuple keys: List of keys to search for
    :rtype: list
    """
    missing_keys = []
    # if the dictionary doesn't exist then all of the keys are missing
    if dictionary is None:
        missing_keys = list(keys)
    else:
        for k in keys:
            if k not in dictionary:
                missing_keys.append(k)

    return missing_keys


def get_install_values(namespace, install_config_secret):
    values_secret = None
    if install_config_secret:
        values_secret = get_install_config_secret(namespace=namespace, install_config_secret=install_config_secret)
        if not values_secret:
            click.echo(f'Cannot find values secret {install_config_secret}. Exiting Install\n')
            sys.exit(1)

    return values_secret


def get_image_and_license_secret_from_values(values_secret, values_file, image_pull_secret, license_secret):
    """Read image_pull_secret and license_secret from argument, values file, values secret, default"""
    values_secret_dict = {}
    if values_secret:
        values_secret_dict = yaml.safe_load(values_secret)

    values_file_dict = {}
    if values_file:
        if not os.path.exists(values_file):
            log.error(f'File not found: {values_file}. Exiting')
            sys.exit(1)
        else:
            with open(values_file) as f:
                try:
                    values_file_dict = yaml.safe_load(f)
                except yaml.YAMLError as e:
                    log.error(f'Invalid values file {values_file}')
                    click.echo(e)
                    sys.exit(1)

    if not image_pull_secret:
        image_pull_secret = get_from_values_dict(['global', 'imagePullSecrets', 0, 'name'], values_secret_dict,
                                                 values_file_dict, default_val('image.pullSecret'))

    if not license_secret:
        license_secret = get_from_values_dict(['global', 'license', 'secretName'], values_secret_dict, values_file_dict,
                                              default_val('license.secret'))

    return image_pull_secret, license_secret


def get_from_values_dict(key, values_secret_dict, values_file_dict, default):
    try:
        val = values_file_dict
        for k in key:
            val = val[k]
        log.debug(f'Using key {key} in values file')
    except KeyError:
        try:
            val = values_secret_dict
            for k in key:
                val = val[k]
            log.debug(f'Using key {key} in values secret')
        except KeyError:
            val = default
            log.debug(f'Cannot find key {key} in values file or secret. Using default.')
        except BaseException as e:
            log.error(f'Invalid values secret')
            log.error(e)
            sys.exit(1)
    except BaseException as e:
        log.error(f'Invalid values file')
        log.error(e)
        sys.exit(1)

    return val


def gen_private_key():
    """Creates a basic private key"""
    log.debug('Generating private key with size 2048 and exponent 65537')

    private_key = asymmetric.rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    return private_key


def gen_cert(private_key):
    """Creates a basic certificate given a public key"""
    log.debug('Generating cert with common name insights.kx.com')

    subject = x509.Name([x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, 'insights.kx.com')])

    # For a self-signed cert, the subject and the issuer are always the same
    builder = x509.CertificateBuilder(
        issuer_name=subject,
        subject_name=subject,
        public_key=private_key.public_key(),
        serial_number=x509.random_serial_number(),
        not_valid_before=datetime.datetime.utcnow(),
        not_valid_after=datetime.datetime.utcnow() + datetime.timedelta(days=3650)
    )

    # This must be set on the generated cert in order of it to be a valid Issuer in kubernetes
    builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(private_key.public_key()),
                                    critical=False)
    builder = builder.add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)

    return builder.sign(private_key, hashes.SHA256(), default_backend())


def install_operator_and_release(release, namespace, version, operator_version, values_file, values_secret,
                                 image_pull_secret, license_secret, chart_repo_name, force, prompt_to_install_operator=True):
    """Install operator and insights"""

    subprocess.run(['helm', 'repo', 'update'], check=True)

    operator_installed_charts = get_installed_charts(release, operator_namespace)
    if len(operator_installed_charts) > 0:
        click.echo(f'\nkxi-operator already installed with version {operator_installed_charts[0]["chart"]}')
    else:
        click.echo(f'\nkxi-operator not found')
        prompt_to_install_operator = True

    operator_version_to_install = get_operator_version(chart_repo_name, version, operator_version)
    if force or prompt_to_install_operator and click.confirm(f'Do you want to install kxi-operator version {operator_version_to_install}?', default=True):
        create_namespace(operator_namespace)

        copy_secret(image_pull_secret, namespace, operator_namespace)
        copy_secret(license_secret, namespace, operator_namespace)

        helm_install(release, chart=f'{chart_repo_name}/kxi-operator', values_file=values_file, values_secret=values_secret, version=operator_version_to_install, namespace=operator_namespace)

    insights_installed_charts = get_installed_charts(release, namespace)
    if len(insights_installed_charts) > 0:
        click.echo(f'\nKX Insights already installed with version {insights_installed_charts[0]["chart"]}')

    helm_install(release, chart=f'{chart_repo_name}/insights', values_file=values_file, values_secret=values_secret, version=version, namespace=namespace)

    return True


def delete_release_operator_and_crds(release, namespace, force):
    """Delete insights, operator and CRDs"""
    if not insights_installed(release, namespace):
        click.echo('\nKX Insights installation not found')
    else:
        if force or click.confirm('\nKX Insights is deployed. Do you want to uninstall?'):
            helm_uninstall(release=release, namespace=namespace)
        else:
            return

    if force or operator_installed(release) and click.confirm(
            '\nThe kxi-operator is deployed. Do you want to uninstall?'):
        helm_uninstall(release=release, namespace=operator_namespace)

    crds = common.get_existing_crds(['assemblies.insights.kx.com', 'assemblyresources.insights.kx.com'])
    if force or len(crds) > 0 and click.confirm(f'\nThe assemblies CRDs {crds} exist. Do you want to delete them?'):
        for i in crds:
            common.delete_crd(i)


def helm_add_repo(chart_repo_name, url, username, password):
    """Call 'helm repo add' using subprocess.run"""
    log.debug(
        'Attempting to call: helm repo add --username {username} --password {len(password)*"*" {chart_repo_name} {url}')
    try:
        subprocess.run(['helm', 'repo', 'add', '--username', username, '--password', password, chart_repo_name, url],
                       check=True)
    except subprocess.CalledProcessError:
        # Pass here so that the password isn't printed in the log
        pass


def helm_list_versions(chart_repo_name):
    """Call 'helm search repo' using subprocess.run"""
    log.debug('Attempting to call: helm search repo')
    try:
        chart = f'{chart_repo_name}/insights'
        click.echo(f'Listing available KX Insights versions in repo {chart_repo_name}')

        subprocess.run(['helm', 'search', 'repo', chart], check=True)
    except subprocess.CalledProcessError as e:
        click.echo(e)


def helm_install(release, chart, values_file, values_secret, version=None, namespace=None):
    """Call 'helm install' using subprocess.run"""

    base_command = ['helm', 'upgrade', '--install']

    if values_secret:
        base_command = base_command + ['-f', '-']
        input_arg = values_secret
        text_arg = True
    else:
        input_arg=None
        text_arg=None

    if values_file: 
        base_command = base_command + ['-f', values_file]

    base_command = base_command + [release, chart]

    version_msg = ''
    if version:
        version_msg = ' version ' + version
        base_command = base_command + ['--version', version]

    if values_file: 
        if values_secret:
            click.echo(f'Installing chart {chart}{version_msg} with values from secret and values file from {values_file}')
        else:
            click.echo(f'Installing chart {chart}{version_msg} with values file from {values_file}')
    else:
        if values_secret:
            click.echo(f'Installing chart {chart}{version_msg} with values from secret')
        else:
            click.echo(f'Must provide one of values file or secret. Exiting install')
            sys.exit(1)

    if namespace:
        base_command = base_command + ['--namespace', namespace]
        create_namespace(namespace)

    try:
        log.debug(f'Install command {base_command}')
        subprocess.run(base_command, check=True, input=input_arg, text=text_arg)
    except subprocess.CalledProcessError as e:
        click.echo(e)
        sys.exit(e.returncode)


def helm_uninstall(release, namespace=None):
    """Call 'helm uninstall' using subprocess.run"""

    msg = f'Uninstalling release {release}'

    base_command = ['helm', 'uninstall', release]

    if namespace:
        base_command = base_command + ['--namespace', namespace]
        msg = f'{msg} in namespace {namespace}'

    click.echo(msg)

    try:
        log.debug(f'Uninstall command {base_command}')
        subprocess.run(base_command, check=True)
    except subprocess.CalledProcessError as e:
        click.echo(e)
        sys.exit(e.returncode)


def create_namespace(name):
    common.load_kube_config()
    api = k8s.client.CoreV1Api()
    ns = k8s.client.V1Namespace()
    ns.metadata = k8s.client.V1ObjectMeta(name=name)
    try:
        api.create_namespace(ns)
    except k8s.client.rest.ApiException as exception:
        # 409 is a conflict, this occurs if the namespace already exists
        if not exception.status == 409:
            log.error(f'Exception when trying to create namespace {exception}')
            sys.exit(1)


def copy_secret(name, from_ns, to_ns):
    common.load_kube_config()
    api = k8s.client.CoreV1Api()
    try:
        secret = api.read_namespaced_secret(namespace=from_ns, name=name)
    except k8s.client.rest.ApiException as exception:
        log.error(f'Exception when trying to get secret {exception}')
        sys.exit(1)

    secret.metadata = k8s.client.V1ObjectMeta(namespace=to_ns, name=name)

    try:
        secret = api.create_namespaced_secret(namespace=to_ns, body=secret)
    except k8s.client.rest.ApiException as exception:
        if not exception.status == 409:
            log.error(f'Exception when trying to create secret {exception}')
            sys.exit(1)


def prompt_for_client_secret(client_name):
    if click.confirm(f'Do you want to set a secret for the {client_name} service account explicitly'):
        client_secret = click.prompt('Please enter the secret (input hidden)', hide_input=True)
    else:
        click.echo(
            f'Randomly generating client secret for {client_name} and setting in values file, record this value for reuse during upgrade')
        client_secret = ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(10))

    return client_secret


def insights_installed(release, namespace):
    """Check if a helm release of insights exists"""
    return len(get_installed_charts(release, namespace)) > 0


def operator_installed(release, namespace: str = operator_namespace):
    """Check if a helm release of the operator exists"""
    return len(get_installed_charts(release, operator_namespace)) > 0

def get_installed_charts(release, namespace):
    """Retrieve running helm charts"""
    base_command = ['helm', 'list', '--filter', release, '--deployed', '-o', 'json','--namespace', namespace]
    try:
        log.debug(f'List command {base_command}')
        l = subprocess.check_output(base_command)
        return json.loads(l)
    except subprocess.CalledProcessError as e:
        click.echo(e)

# Check if Keycloak is being deployed with Insights
def deploy_keycloak():
    return '--keycloak-auth-url' not in sys.argv
