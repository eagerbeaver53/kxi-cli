"""This end 2 end test validates the inputs and outputs of the install command directly"""
import copy
import filecmp
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import mkdtemp
from typing import List, Optional
from unittest.mock import MagicMock
import pyk8s
import pytest
from pytest_mock import MockerFixture

import yaml, json
from click.testing import CliRunner
from kxicli import common
from kxicli import main
from kxicli import phrases
from kxicli.resources.helm import LocalHelmVersion, minimum_helm_version, HelmVersionChecked, \
    required_helm_version
from kxicli.commands.assembly import CONFIG_ANNOTATION
from kxicli.commands import install

import utils
from cli_io import cli_input, cli_output
from const import test_namespace,  test_chart_repo_name, test_chart_repo_url, \
    test_user, test_pass, test_docker_config_json, test_cert, test_key, test_ingress_cert_secret, \
    test_management_namespace

common.config.config_file = os.path.dirname(__file__) + '/files/test-cli-config'
common.config.load_config("default")

GET_ASSEMBLIES_LIST_FUNC='kxicli.commands.assembly.get_assemblies_list'
LIST_CLUSTER_ASSEMBLIES_FUNC='kxicli.commands.assembly.list_cluster_assemblies'
DELETE_ASSEMBLIES_FUNC='kxicli.commands.assembly._delete_assembly'
TEST_VALUES_FILE="a test values file"

test_auth_url = 'http://keycloak.keycloak.svc.cluster.local/auth/'
test_chart_repo = 'kx-insights'
test_chart = f'{test_chart_repo}/insights'
test_chart_repo_url_oci = 'oci://kxinsightsprod.azurecr.io'
test_operator_chart = 'kx-insights/kxi-operator'
test_operator_helm_name = 'test-op-helm'
test_management_version = '0.1.3'
test_management_chart = 'kx-insights/kxi-management'

test_k8s_config = str(Path(__file__).parent / 'files' / 'test-kube-config')
test_cli_config_static = common.config.config_file
expected_test_output_file = str(Path(__file__).parent / 'files' / 'output-values.yaml')
test_output_file_lic_env_var = str(Path(__file__).parent / 'files' / 'output-values-license-as-env-var.yaml')
test_output_file_lic_on_demand = str(Path(__file__).parent / 'files' / 'output-values-license-on-demand.yaml')
test_output_file_manual_ingress = str(Path(__file__).parent / 'files' / 'output-values-manual-ingress-secret.yaml')
test_output_file_external_certmanager = str(Path(__file__).parent / 'files' / 'output-values-external-certmanager.yaml')
test_output_file_updated_hostname = str(Path(__file__).parent / 'files' / 'output-values-updated-hostname.yaml')
test_output_file_updated_passwords_config = str(Path(__file__).parent / 'files' / 'output-values-updated-client-passwords.yaml')
test_output_file_updated_passwords_cmd_line = str(Path(__file__).parent / 'files' / 'output-values-updated-client-passwords-from-command-line.yaml')
test_val_file_shared_keycloak = str(Path(__file__).parent / 'files' / 'test-values-shared-keycloak.yaml')
test_asm_name = 'basic-assembly'  # As per contents of test_asm_file
test_asm_name2 = 'basic-assembly2'  # As per contents of test_asm_file2
test_asm_backup =  str(Path(__file__).parent / 'files' / 'test-assembly-backup.yaml')
test_crds = ['assemblies.insights.kx.com', 'assemblyresources.insights.kx.com']
config_json_file_name: str = 'config.json'

with open(utils.test_val_file, 'rb') as values_file:
    test_vals = yaml.full_load(values_file)

# Read in slow-to-load CRD data once so it isn't loaded on each test
# as reading this can take up to 5s
GLOBAL_CRD_DATA = []
tar_path = Path(__file__).parent / 'files/helm/kxi-operator-1.2.3.tgz'
files = [f'kxi-operator/crds/{crd}' for crd in install.CRD_FILES]
raw_data = common.extract_files_from_tar(tar_path, files)
for blob in raw_data:
    GLOBAL_CRD_DATA.append(yaml.safe_load(blob))

def mock_read_cached_crd_data(*args, **kwargs):
    print(f"Reading CRD data from {tar_path}")
    return GLOBAL_CRD_DATA


helm_add_repo_params = ()
delete_crd_params = []
delete_assembly_args = []
insights_installed_flag = True
operator_installed_flag = True
crd_exists_flag = True
running_assembly = {}
copy_secret_params = []
subprocess_run_command = []

# override where the command looks for the docker config json
# by default this is $HOME/.docker/config.json
main.install.DOCKER_CONFIG_FILE_PATH = test_docker_config_json

# Tell cli that this is an interactive session
common.is_interactive_session = utils.return_true

@contextmanager
def temp_test_output_file(prefix: str = 'kxicli-e2e-', file_name='output-values.yaml'):
    dir_name: str = str()
    inited: bool = False
    try:
        dir_name = mkdtemp(prefix=prefix)
        inited = True
        output_file_name = str(Path(dir_name).joinpath(file_name))
        yield output_file_name
    finally:
        if inited:
            shutil.rmtree(dir_name)


@contextmanager
def temp_config_file(prefix: str = 'kxicli-config-', file_name='test-cli-config'):
    dir_name: str = str()
    inited: bool = False
    try:
        dir_name = mkdtemp(prefix=prefix)
        inited = True
        output_file_name = str(Path(dir_name).joinpath(file_name))
        shutil.copyfile(common.config.config_file, output_file_name)
        common.config.config_file = output_file_name
        yield output_file_name
    finally:
        if inited:
            shutil.rmtree(dir_name)
            common.config.config_file = test_cli_config_static
            common.config.load_config("default")


def compare_files(file1: str, file2: str):
    if os.name == 'nt':
        with temp_test_output_file() as temp_file1:
            with open(temp_file1, 'w', newline='\n') as tf, open(file1, 'r') as of:
                for line in of.readlines():
                    tf.write(line)
            return filecmp.cmp(temp_file1, file2, shallow=False)
    else:
        return filecmp.cmp(file1, file2, shallow=False)


def mocked_read_secret(namespace=None,
                        name=None,
                        image_pull_secret_name=common.get_default_val('image.pullSecret'),
                        ):
    return utils.fake_docker_config_secret


def mocked_helm_add_repo(repo, url, username, password):
    global helm_add_repo_params
    helm_add_repo_params = (repo, url, username, password)
    pass


def mocked_helm_list_returns_empty_json(*args, **kwargs):
    return '[]'


def mocked_empty_list():
    return []

def mock_empty_helm_repo_list(mocker):
    mocker.patch('kxicli.commands.install.helm.repo_list', mocked_empty_list)

def mocked_helm_version_checked():
    helm_version_checked: HelmVersionChecked = HelmVersionChecked(
        req_helm_version=required_helm_version,
        local_helm_version=LocalHelmVersion(minimum_helm_version)
    )
    return helm_version_checked

def mock_delete_crd(mocker: MockerFixture, k8s: MagicMock):
    global delete_crd_params
    delete_crd_params = []
    global crd_exists_flag
    crd_exists_flag = True
    utils.mock_kube_crd_api(k8s,
                            create=mocked_create_crd,
                            delete=mocked_delete_crd
                            )
    mocker.patch.object(pyk8s.models.V1CustomResourceDefinition, "wait_until_not_ready")

def mocked_delete_crd(name=None, **kwargs):
    global delete_crd_params
    delete_crd_params.append(name)
    global crd_exists_flag
    crd_exists_flag = False


def mocked_create_crd(name):
    global crd_exists_flag
    crd_exists_flag = True

def mocked_copy_secret(name, from_ns, to_ns):
    global copy_secret_params
    copy_secret_params.append((name, from_ns, to_ns))


def mock_copy_secret(mocker, k8s):
    global copy_secret_params
    copy_secret_params = []
    mocker.patch('kxicli.commands.install.copy_secret', mocked_copy_secret)


def mocked_k8s_list_empty_config():
    return ([], {'context': ()})


def mocked_create_namespace(namespace):
    # Test function to mock
    pass


def mocked_get_operator_version(chart_repo_name, insights_version, operator_version):
    if operator_version:
        return operator_version
    else:
        return insights_version


def mock_get_operator_version(mocker):
    mocker.patch('kxicli.commands.install.get_operator_version', mocked_get_operator_version)


def mocked_get_management_version(chart_repo_name, management_version):
    if management_version:
        return management_version
    else:
        return test_management_version


def mock_get_management_version(mocker):
    mocker.patch('kxicli.commands.install.get_management_version', mocked_get_management_version)

def mocked_installed_chart_json(release, namespace):
    return [{"name":"insights","namespace":"testNamespace","revision":"1","updated":"2022-02-23 10:39:53.7668809 +0000 UTC","status":"deployed","chart":"insights-1.2.1","app_version":"1.2.1"}]

def mocked_installed_chart_management_json(release, namespace):
    return [{"name":"kxi-management-service","namespace":"kxi-management","revision":"1","updated":"2022-02-23 10:39:53.7668809 +0000 UTC","status":"deployed","chart":"kxi-management-service-0.1.4","app_version":"0.1.2"}]

def mocked_helm_list_returns_valid_json(release, namespace):
    if insights_installed_flag and namespace == test_namespace:
        return mocked_installed_chart_json(release, namespace)
    else:
        return []

def mocked_helm_list_returns_valid_json_management(release, namespace):
    if insights_installed_flag and namespace == test_namespace:
        return mocked_installed_chart_json(release, namespace)
    elif insights_installed_flag and namespace == test_management_namespace:
        return mocked_installed_chart_management_json(release, namespace)
    else:
        return []
    


def mocked_installed_operator_versions(namespace):
    return (['1.2.0'], [test_operator_helm_name])

def mocked_get_installed_operator_versions(namespace):
    if operator_installed_flag:
        return mocked_installed_operator_versions(namespace)
    else:
        return ([], [])


def mocked_get_installed_operator_versions_without_release(namespace):
        return (['1.2.0'], [None])

def mocked_get_installed_operator_versions_without_release_14(namespace):
        return (['1.4.0'], [None])


def mocked_subprocess_run(
        *popenargs, **kwargs
):
    global insights_installed_flag
    global operator_installed_flag
    global crd_exists_flag
    global subprocess_run_command
    env: dict = kwargs.get('env')
    res_item = SubprocessRunInput(cmd=popenargs[0],
                                  env=env,
                                  kwargs=kwargs
                                  )
    try:
        with open(str(Path(env['DOCKER_CONFIG']) / config_json_file_name)) as dc:
            res_item.dockerconfigjson = dc.read()
    except BaseException:
        pass
    subprocess_run_command.append(res_item)
    if res_item.cmd == ['helm', 'uninstall', 'insights', '--namespace', test_namespace]:
        insights_installed_flag = False
    elif res_item.cmd == ['helm', 'uninstall', 'insights', '--namespace', 'kxi-operator']:
        operator_installed_flag = False
    elif res_item.cmd[0:3]+res_item.cmd[-2:] == ['helm', 'upgrade', '--install', '--namespace', 'kxi-operator']:
        operator_installed_flag = True
    elif res_item.cmd[0:3]+res_item.cmd[-2:] == ['helm', 'upgrade', '--install', '--namespace', test_namespace]:
        insights_installed_flag = True
        crd_exists_flag = True
    return CompletedProcess(args=popenargs[0], returncode=0, stdout='subprocess_stdout', stderr='subprocess_stderr')


def mock_subprocess_run(mocker):
    global subprocess_run_command
    subprocess_run_command = []
    mocker.patch('subprocess.run', mocked_subprocess_run)


def mock_set_insights_operator_and_crd_installed_state(mocker, insights_flag, operator_flag, crd_flag):
    global insights_installed_flag
    global operator_installed_flag
    global crd_exists_flag
    insights_installed_flag = insights_flag
    operator_installed_flag = operator_flag
    crd_exists_flag = crd_flag
    mocker.patch('kxicli.commands.install.insights_installed', mocked_insights_installed)
    mocker.patch('kxicli.common.crd_exists', mocked_crd_exists)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions)


def mocked_insights_installed(release, namespace):
    return insights_installed_flag


def mocked_operator_installed(release):
    return operator_installed_flag


def mocked_crd_exists(name):
    return crd_exists_flag


def mock_secret_helm_add(mocker, k8s):
    utils.mock_kube_secret_api(k8s, read=utils.raise_not_found)
    mock_empty_helm_repo_list(mocker)
    helm_add_repo_params = ()
    mocker.patch('kxicli.commands.install.helm.add_repo', mocked_helm_add_repo)


def mock_list_assembly_none(namespace):
    return []


def mock_list_assembly(namespace):
    with open(utils.test_asm_file) as f:
        test_asm = yaml.safe_load(f)
    return [test_asm]


def mock_list_assembly_multiple(*args, **kwargs):
    with open(utils.test_asm_file) as f:
        test_asm = yaml.safe_load(f)
        test_asm['metadata']['namespace'] = utils.namespace()
    with open(utils.test_asm_file2) as f:
        test_asm2 = yaml.safe_load(f)
        test_asm2['metadata']['namespace'] = utils.namespace()

    return [test_asm, test_asm2]


def mock_delete_assembly(mocker, k8s):
    PREFERRED_VERSION_FUNC = 'kxicli.commands.assembly.get_preferred_api_version'
    PREFERRED_VERSION = 'v1'
    mocker.patch(PREFERRED_VERSION_FUNC, return_value=PREFERRED_VERSION)
    k8s.assemblies.delete.return_value = utils.return_none
    k8s.assemblies.read.side_effect = utils.raise_not_found


def mock_create_assembly(hostname, realm, namespace, body, *args):
    asm_name = body['metadata']['name']
    print(f'Custom assembly resource {asm_name} created!')
    running_assembly[asm_name] = True
    return True

def mock__delete_assembly(namespace, name, wait, force, **kwargs):
    global delete_assembly_args
    if 'delete_assembly_args' in kwargs:
        delete_assembly_args = kwargs.get('delete_assembly_args')
    delete_assembly_args.append({'name':name, 'namespace':namespace})
    return True


def mocked_generate_password():
    return 'aRandomPassword'


def mock_generate_password(mocker):
    mocker.patch('kxicli.options.generate_password', mocked_generate_password)


def mock_asm_backup_path(mocker):
    mocker.patch('kxicli.commands.assembly._backup_filepath', lambda filepath, force: test_asm_backup)


def setup_mocks(mocker, k8s):
    mock_secret_helm_add(mocker, k8s)
    mock_generate_password(mocker)

def upgrades_mocks(mocker, k8s):
    mock_subprocess_run(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mock_copy_secret(mocker, k8s)
    mock_delete_crd(mocker, k8s)
    mock_delete_assembly(mocker, k8s)
    utils.mock_helm_repo_list(mocker)
    global running_assembly
    running_assembly = {test_asm_name:True}
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)
    mocker.patch('kxicli.commands.assembly._create_assembly', mock_create_assembly)


def install_setup_output_check(mocker, test_cfg, expected_exit_code, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file]
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, expected_exit_code)


def run_cli(cmd, test_cfg, cli_config = None, output_file = None, expected_exit_code = 0):
    runner = CliRunner()
    with runner.isolated_filesystem():
        verb = cmd[1]
        user_input = cli_input(verb, **test_cfg)
        expected_output = cli_output(verb, cli_config, output_file, **test_cfg)
        result = runner.invoke(main.cli, cmd, input=user_input)

    assert result.output == expected_output
    assert result.exit_code == expected_exit_code


@dataclass
class HelmCommand():
    version: str = '1.2.3'
    values: str = utils.test_val_file
    release: str = 'insights'
    chart: str = test_chart
    namespace: str = test_namespace
    keycloak_importUsers: Optional[str] = 'true'
    helm_cmd: list = field(default_factory=list),
    management_version: str = '0.1.3'
    management_release: str = 'kxi-management-service'
    management_namespace: str = 'kxi-management'
    management_chart: str = f'{test_chart_repo}/kxi-management-service'

    def cmd(self):
        return self.helm_cmd


@dataclass
class HelmCommandRepoUpdate(HelmCommand):
    repo: str = test_chart_repo

    def cmd(self):
        cmd = ['helm', 'repo', 'update']
        if self.repo:
            cmd = cmd + [self.repo]
        return cmd


@dataclass
class HelmCommandInsightsInstall(HelmCommand):
    def cmd(self):
        cmd = [
            'helm', 'upgrade', '--install',
            '--version', self.version,
            '-f', self.values,
            self.release,
            self.chart
        ]
        if self.keycloak_importUsers:
            cmd = cmd + [ '--set', f'keycloak.importUsers={self.keycloak_importUsers}' ]
        cmd = cmd + ['--namespace', self.namespace]
        return cmd


@dataclass
class HelmCommandOperatorInstall(HelmCommandInsightsInstall):
    chart: str = f'{test_chart_repo}/kxi-operator'
    namespace: str = 'kxi-operator'
    keycloak_importUsers: Optional[str] = None


class HelmCommandManagementInstall(HelmCommand):
    def cmd(self):
        cmd = [
            'helm', 'upgrade', '--install',
            '--version', self.management_version,
            '-f', self.values,
            self.management_release,
            self.management_chart,
            '--namespace', self.management_namespace
        ]
        return cmd

class HelmCommandManagementInstallOverride(HelmCommandManagementInstall):
    management_chart: str = f'{test_chart_repo}/kxi-management-service'
@dataclass
class HelmCommandDelete(HelmCommand):
    def cmd(self):
        return [
            'helm', 'uninstall',
            self.release,
            '--namespace', self.namespace
        ]


def default_helm_commands():
    repo_update_command = HelmCommandRepoUpdate()
    operator_command = HelmCommandOperatorInstall(values = '-', release = test_operator_helm_name)
    insights_command = HelmCommandInsightsInstall(values = '-', keycloak_importUsers= 'false')
    management_command = HelmCommandManagementInstall()
    return repo_update_command, operator_command, insights_command, management_command


def default_helm_commands():
    repo_update_command = HelmCommandRepoUpdate()
    operator_command = HelmCommandOperatorInstall(values = '-', release = test_operator_helm_name)
    insights_command = HelmCommandInsightsInstall(values = '-', keycloak_importUsers= 'false')
    management_command = HelmCommandManagementInstallOverride(values = '-')
    return repo_update_command, operator_command, insights_command, management_command


def check_subprocess_run_commands(helm_commands):
    assert len(subprocess_run_command) == len(helm_commands)
    for i in range(len(subprocess_run_command)):
        assert subprocess_run_command[i].cmd == helm_commands[i].cmd()


def install_upgrade_checks(result,
                           helm_commands=default_helm_commands(),
                           docker_config_check=True,
                           expected_subprocess_args=[True, yaml.dump(utils.test_val_data), True],
                           expected_delete_crd_params=test_crds,
                           expected_running_assembly={test_asm_name:True},
                        ):
    assert result.exit_code == 0
    check_subprocess_run_commands(helm_commands)
    if docker_config_check:
        insights_install = subprocess_run_command[1]
        assert 'DOCKER_CONFIG' in dict(insights_install.env)
        assert insights_install.dockerconfigjson == utils.fake_docker_config_yaml
    assert [subprocess_run_command[-2].kwargs.get(key) for key in ['check', 'input', 'text']] == expected_subprocess_args
    assert delete_crd_params == expected_delete_crd_params
    assert insights_installed_flag == True
    assert operator_installed_flag == True
    assert crd_exists_flag == True
    assert running_assembly == expected_running_assembly
    assert not os.path.isfile(test_asm_backup)


@dataclass
class SubprocessRunInput():
    cmd: List[str]
    dockerconfigjson: str = ''
    kwargs: list = field(default_factory=dict)
    env: list = field(default_factory=dict)

@pytest.fixture()
def cleanup_env_globals():
    global running_assembly
    global delete_crd_params
    running_assembly = {}
    delete_crd_params = []

# Tests

def test_install_setup_when_creating_secrets(mocker, k8s):
    install_setup_output_check(mocker, {}, 0, k8s)


def test_install_setup_when_using_existing_docker_creds(mocker, k8s):
    test_cfg = {
        'use_existing_creds': 'y'
    }
    install_setup_output_check(mocker, test_cfg, 0, k8s)


def test_install_setup_when_reading_client_passwords_from_cli_config(mocker, k8s):
    setup_mocks(mocker, k8s)

    common.config.config_file = os.path.dirname(__file__) + '/files/test-cli-config-client-secrets'
    test_cfg = {
        'gui_secret_source': 'config',
        'operator_secret_source': 'config'
    }
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file]
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, test_output_file_updated_passwords_config)
        with open(test_cli_config, "r") as f:
            assert f.read() == """[default]
usage = enterprise
hostname = https://test.kx.com
namespace = test
client.id = client
client.secret = secret
guiClientSecret = gui-secret
operatorClientSecret = operator-secret
"""
    common.config.load_config("default")


def test_install_setup_when_reading_client_passwords_from_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)

    common.config.config_file = os.path.dirname(__file__) + '/files/test-cli-config-client-secrets'
    test_cfg = {
        'gui_secret_source': 'command-line',
        'operator_secret_source': 'command-line'
    }
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file, '--gui-client-secret',
            'gui-secret-command-line', '--operator-client-secret', 'operator-secret-command-line']
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, test_output_file_updated_passwords_cmd_line)
        with open(test_cli_config, "r") as f:
            assert f.read() == """[default]
usage = enterprise
hostname = https://test.kx.com
namespace = test
client.id = client
client.secret = secret
guiClientSecret = gui-secret-command-line
operatorClientSecret = operator-secret-command-line

"""
    common.config.load_config("default")


def test_install_setup_when_secret_exists_but_is_invalid(mocker, k8s):
    test_cfg = {
        'lic_sec_exists': True,
        'lic_sec_is_valid': False,
        'image_sec_exists': True,
        'image_sec_is_valid': False,
        'client_sec_exists': True,
        'client_sec_is_valid': False,
        'kc_secret_exists': True,
        'kc_secret_is_valid': False,
        'pg_secret_exists': True,
        'pg_secret_is_valid': False
    }
    utils.mock_validate_secret(mocker, is_valid=False)
    install_setup_output_check(mocker, test_cfg, 0, k8s)


def test_install_setup_when_secrets_exist_and_are_valid(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file]
        test_cfg = {
            'lic_sec_exists': True,
            'image_sec_exists': True,
            'client_sec_exists': True,
            'kc_secret_exists': True,
            'pg_secret_exists': True
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, utils.test_val_file)
        with open(test_cli_config, "r") as f:
            assert f.read() == """[default]
usage = enterprise
hostname = https://test.kx.com
namespace = test
client.id = client
client.secret = secret
auth.serviceaccount.id = test_id
auth.serviceaccount.secret = test_client_id
guiClientSecret = aRandomPassword
operatorClientSecret = aRandomPassword

"""


def test_install_setup_check_output_values_file(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file]
        run_cli(cmd, {}, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, expected_test_output_file)


def test_install_setup_when_hostname_provided_from_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file, '--hostname', 'https://a-test-hostname.kx.com']
        test_cfg = {
            'hostname': 'https://a-test-hostname.kx.com',
            'hostname_source': 'command-line'
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_updated_hostname)


def test_install_setup_ingress_host_is_an_alias_for_hostname(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file, '--ingress-host', 'https://a-test-hostname.kx.com']
        test_cfg = {
            'hostname': 'https://a-test-hostname.kx.com',
            'hostname_source': 'command-line'
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_updated_hostname)


def test_install_setup_when_ingress_cert_secret_provided_on_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker, exists=True, is_valid=True)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file,  '--ingress-cert-secret', test_ingress_cert_secret]
        test_cfg = {
            'provide_ingress_cert': None,
            'ingress_sec_exists': True,
            'lic_sec_exists': True,
            'image_sec_exists': True,
            'client_sec_exists': True,
            'kc_secret_exists': True,
            'pg_secret_exists': True,
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_manual_ingress)


def test_install_setup_when_ingress_cert_and_key_files_provided_on_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker, exists=False, is_valid=True)
    with temp_test_output_file() as test_output_file, temp_test_output_file(file_name='tls_crt') as test_cert_filepath, \
        temp_test_output_file(file_name='tls_key') as test_key_filepath, temp_config_file() as test_cli_config:
        shutil.copyfile(test_cert, test_cert_filepath)
        shutil.copyfile(test_key, test_key_filepath)
        cmd = ['install', 'setup', '--output-file', test_output_file, '--ingress-cert', test_cert_filepath, '--ingress-key', test_key_filepath]
        test_cfg = {
            'provide_ingress_cert': 'y',
            'ingress_cert': test_cert_filepath,
            'ingress_cert_source': 'command-line',
            'ingress_key': test_key_filepath,
            'ingress_key_source': 'command-line',
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_manual_ingress)


def test_install_setup_when_ingress_cert_file_provided_on_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker, exists=False, is_valid=True)
    with temp_test_output_file() as test_output_file, temp_test_output_file(file_name='tls_crt') as test_cert_filepath, \
        temp_test_output_file(file_name='tls_key') as test_key_filepath, temp_config_file() as test_cli_config:
        shutil.copyfile(test_cert, test_cert_filepath)
        shutil.copyfile(test_key, test_key_filepath)
        cmd = ['install', 'setup', '--output-file', test_output_file, '--ingress-cert', test_cert_filepath]
        test_cfg = {
            'provide_ingress_cert': 'y',
            'ingress_cert': test_cert_filepath,
            'ingress_cert_source': 'command-line',
            'ingress_key': test_key_filepath,
            'ingress_key_source': 'prompt',
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_manual_ingress)


def test_install_setup_with_external_ingress_certmanager(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file,  '--ingress-certmanager-disabled']
        test_cfg = {
            'provide_ingress_cert': None,
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_output_file_external_certmanager)


def test_install_setup_when_passed_license_env_var_in_command_line(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file, '--license-as-env-var', 'True']
        test_cfg = {
            'lic_sec_exists': True,
            'image_sec_exists': True,
            'client_sec_exists': True,
            'kc_secret_exists': True,
            'pg_secret_exists': True
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, test_output_file_lic_env_var)


def test_install_setup_when_passed_kc_license_filename(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config, temp_test_output_file(
            file_name='kc.lic') as test_kc_lic:
        with open(test_kc_lic, 'w') as f:
            f.write('This is a test kc license')
        cmd = ['install', 'setup', '--output-file', test_output_file]
        test_cfg = {
            'lic': test_kc_lic
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, test_output_file_lic_on_demand)


def test_install_setup_when_providing_license_secret(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_validate_secret(mocker)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        cmd = ['install', 'setup', '--output-file', test_output_file, '--license-secret', common.get_default_val('license.secret')]
        test_cfg = {
            'lic_sec_exists': True,
            'image_sec_exists': True,
            'client_sec_exists': True,
            'kc_secret_exists': True,
            'pg_secret_exists': True
        }
        mocker.patch('sys.argv', cmd)
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, utils.test_val_file)


def test_install_setup_overwrites_when_values_file_exists(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        with open(test_output_file, 'w') as f:
            f.write(TEST_VALUES_FILE)

        cmd = ['install', 'setup', '--output-file', test_output_file]
        test_cfg = {
            'values_exist': True,
            'overwrite_values': 'y'
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)

        assert compare_files(test_output_file, utils.test_val_file)


def test_install_setup_creates_new_when_values_file_exists(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        with open(test_output_file, 'w') as f:
            f.write(TEST_VALUES_FILE)

        cmd = ['install', 'setup', '--output-file', test_output_file]
        test_cfg = {
            'values_exist': True,
            'overwrite_values': 'n',
            'output_file': test_output_file
        }

        runner = CliRunner()
        with runner.isolated_filesystem():
            user_input = cli_input(cmd[1], **test_cfg)
            result = runner.invoke(main.cli, cmd, input=user_input)
            expected_output = cli_output(cmd[1], test_cli_config, **test_cfg)

        assert result.exit_code == 0
        assert result.output == expected_output
        assert compare_files(f'{test_output_file}_new', utils.test_val_file)
        with open(test_output_file, "r") as f:
            assert f.read() == TEST_VALUES_FILE # assert that the original file is unchanged


def test_install_setup_runs_in_cluster(mocker, k8s):
    setup_mocks(mocker, k8s)
    test_ns = utils.namespace()
    k8s.in_cluster = True
    mocker.patch('kxicli.options.get_namespace', lambda: test_ns)
    test_cfg = {
        'incluster': True
    }
    install_setup_output_check(mocker, test_cfg, 0, k8s)


def test_install_run_when_provided_file(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file],
            input='n'
        )
        expected_output = f"""{phrases.values_validating}

kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: n
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandInsightsInstall(),
        HelmCommandManagementInstall()
    ])


def test_install_run_when_no_file_provided(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mock_subprocess_run(mocker)
    mocker.patch('subprocess.check_output', mocked_helm_list_returns_empty_json)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_validate_secret(mocker)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        with open(test_output_file, 'w') as f:
            f.write(TEST_VALUES_FILE)

        runner = CliRunner()
        with runner.isolated_filesystem():
            test_cfg = {
                'lic_sec_exists': True,
                'image_sec_exists': True,
                'client_sec_exists': True,
                'kc_secret_exists': True,
                'pg_secret_exists': True,
            }
            user_input = f'{cli_input("setup", **test_cfg)}\nn'
            result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3'], input=user_input)

            expected_output = f"""{phrases.header_run}
{cli_output('setup', test_cli_config, 'values.yaml', **test_cfg)}{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: n
Installing chart kx-insights/insights version 1.2.3 with values file from values.yaml

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from values.yaml

Install complete for the KXI Management Service
"""

        assert result.exit_code == 0
        assert result.output == expected_output
        check_subprocess_run_commands([
            HelmCommandRepoUpdate(repo='kx-insights'),
            HelmCommandInsightsInstall(values = 'values.yaml', chart = 'kx-insights/insights'),
            HelmCommandManagementInstall(values = 'values.yaml',)
        ])

def test_install_run_when_no_file_provided_import_users_false(mocker, k8s):
    setup_mocks(mocker, k8s)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mock_subprocess_run(mocker)
    mocker.patch('subprocess.check_output', mocked_helm_list_returns_empty_json)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_validate_secret(mocker)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        with open(test_output_file, 'w') as f:
            f.write(TEST_VALUES_FILE)

        runner = CliRunner()
        with runner.isolated_filesystem():
            test_cfg = {
                'lic_sec_exists': True,
                'image_sec_exists': True,
                'client_sec_exists': True,
                'kc_secret_exists': True,
                'pg_secret_exists': True,
            }
            user_input = f'{cli_input("setup", **test_cfg)}\nn'
            result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3', '--import-users', 'False'], input=user_input)

            expected_output = f"""{phrases.header_run}
{cli_output('setup', test_cli_config, 'values.yaml', **test_cfg)}{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: n
Installing chart kx-insights/insights version 1.2.3 with values file from values.yaml

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from values.yaml

Install complete for the KXI Management Service
"""

        assert result.exit_code == 0
        assert result.output == expected_output
        check_subprocess_run_commands([
            HelmCommandRepoUpdate(repo='kx-insights'),
            HelmCommandInsightsInstall(values = 'values.yaml',
                                   chart = 'kx-insights/insights',
                                   keycloak_importUsers='false'
                                   ),
            HelmCommandManagementInstall(values = 'values.yaml')
        ])


def test_install_run_installs_operator(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    global copy_secret_params
    copy_secret_params = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file],
                               input=user_input)
        expected_output = f"""{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert copy_secret_params == [('kxi-nexus-pull-secret', test_namespace, 'kxi-operator'),
                                ('kxi-license', test_namespace, 'kxi-operator'),
                                ('kxi-nexus-pull-secret', 'test-namespace', 'kxi-management'), 
                                ('kxi-license', 'test-namespace', 'kxi-management')]
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(),
        HelmCommandInsightsInstall(),
        HelmCommandManagementInstall()
    ])


def test_install_run_when_provided_oci_chart_repo_url(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mocker.patch('kxicli.resources.helm.get_helm_version_checked', mocked_helm_version_checked)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'run', '--filepath', utils.test_val_file, '--version', '1.2.3','--chart-repo-url', test_chart_repo_url_oci],
            input='y'
        )
        expected_output = f"""{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Installing chart {test_chart_repo_url_oci}/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Installing chart {test_chart_repo_url_oci}/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart {test_chart_repo_url_oci}/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([
        HelmCommandOperatorInstall(chart=test_chart_repo_url_oci+'/kxi-operator'),
        HelmCommandInsightsInstall(chart=test_chart_repo_url_oci+'/insights'),
        HelmCommandManagementInstallOverride(management_chart=test_chart_repo_url_oci+'/kxi-management-service')
    ])


def test_install_run_force_installs_operator(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    global copy_secret_params
    copy_secret_params = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
                               ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file, '--force'])
        expected_output = f"""{phrases.values_validating}

Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert copy_secret_params == [('kxi-nexus-pull-secret', test_namespace, 'kxi-operator'),
                                ('kxi-license', test_namespace, 'kxi-operator'),
                                ('kxi-nexus-pull-secret', 'test-namespace', 'kxi-management'), 
                                ('kxi-license', 'test-namespace', 'kxi-management')]
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(),
        HelmCommandInsightsInstall(),
        HelmCommandManagementInstall()
    ])


def test_install_run_with_operator_version(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    global copy_secret_params
    copy_secret_params = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
                               ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file, '--operator-version', '1.2.1'])
        expected_output = f"""{phrases.values_validating}

Installing chart kx-insights/kxi-operator version 1.2.1 with values file from {utils.test_val_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert copy_secret_params == [('kxi-nexus-pull-secret', test_namespace, 'kxi-operator'),
                                ('kxi-license', test_namespace, 'kxi-operator'),
                                ('kxi-nexus-pull-secret', 'test-namespace', 'kxi-management'), 
                                ('kxi-license', 'test-namespace', 'kxi-management')]
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(version='1.2.1'),
        HelmCommandInsightsInstall(),
        HelmCommandManagementInstall()
    ])

def test_install_run_with_no_operator_version_available(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mocker.patch('kxicli.commands.install.get_operator_version', utils.return_none)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    global copy_secret_params
    copy_secret_params = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
                               ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file])
        expected_output = f"""{phrases.values_validating}

Error: Compatible version of operator not found
"""
    assert result.exit_code == 1
    assert result.output == expected_output
    assert copy_secret_params == []
    check_subprocess_run_commands([HelmCommandRepoUpdate()])


def test_install_run_with_compitable_operator_already_installed(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, True, True)
    mocker.patch('kxicli.commands.install.get_operator_version', utils.return_none)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
                               ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file])
        expected_output = f"""{phrases.values_validating}

kxi-operator already installed with version 1.2.0
Not installing kxi-operator
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert copy_secret_params == [('kxi-nexus-pull-secret', 'test-namespace', 'kxi-management'), 
                                ('kxi-license', 'test-namespace', 'kxi-management')]
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandInsightsInstall(),
        HelmCommandManagementInstall()
    ])


def test_install_run_installs_operator_with_modified_secrets(mocker, k8s):
    new_image_secret = 'new-image-pull-secret'
    new_lic_secret = 'new-license-secret'

    read_secret_func = partial(mocked_read_secret, image_pull_secret_name=new_image_secret)
    mock_subprocess_run(mocker)
    utils.mock_kube_secret_api(k8s, read=read_secret_func)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)

    runner = CliRunner()
    with utils.temp_file('values.yaml') as values_file:
        content = copy.deepcopy(utils.test_val_data)
        content['global']['imagePullSecrets'][0]['name'] = new_image_secret
        content['global']['license']['secretName'] = new_lic_secret
        with open(values_file, mode='w') as f:
            f.write(yaml.safe_dump(content))

        user_input = "y\n"
        result = runner.invoke(main.cli,
                               ['install', 'run', '--version', '1.2.3', '--filepath', values_file],
                               input=user_input)

    expected_output = f"""{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {values_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {values_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {values_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert copy_secret_params == [(new_image_secret, test_namespace, 'kxi-operator'),
                                (new_lic_secret, test_namespace, 'kxi-operator'),
                                (new_image_secret, test_namespace, 'kxi-management'), 
                                (new_lic_secret, test_namespace, 'kxi-management')]
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(values=values_file),
        HelmCommandInsightsInstall(values=values_file),
        HelmCommandManagementInstall(values=values_file)
    ])
    assert [subprocess_run_command[1].kwargs.get(key) for key in ['check', 'input', 'text']] == [True, None, None]


def test_install_run_when_no_context_set(mocker, k8s):
    k8s.config.namespace = None
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file],
            input='n'
        )
        expected_output = f"""Using namespace from config file {test_cli_config_static}: {utils.namespace()}
Validating values...

kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: n
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([
        HelmCommandRepoUpdate(),
        HelmCommandInsightsInstall(namespace=utils.namespace()),
        HelmCommandManagementInstall()
    ])


def test_install_run_exits_when_already_installed(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_repo_list(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file], input ='n\n')
        expected_output = f"""{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
Would you like to upgrade to version 1.2.3? [y/N]: n
"""

    assert result.exit_code == 0
    assert result.output == expected_output


def test_install_run_prompts_when_repo_does_not_exist(mocker, k8s):
    # return an empty repo list, then a populated one after the repo has been added
    helm_list = [
        [],
        [{'name': test_chart_repo, 'url': test_chart_repo_url}]
    ]
    mocker.patch('kxicli.resources.helm.repo_list', side_effect=helm_list)
    mocker.patch('kxicli.resources.helm.add_repo', mocked_helm_add_repo)
    mocker.patch('kxicli.resources.helm.repo_update')
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    runner = CliRunner()
    user_input = f"""
{test_user}
{test_pass}
{test_pass}
"""

    expected_output = f"""{phrases.values_validating}

Please enter the chart repository URL to pull charts from [https://nexus.dl.kx.com/repository/kx-insights-charts]: 
Please enter the username for the chart repository: {test_user}
Please enter the password for the chart repository (input hidden): 
Re-enter to confirm (input hidden): 
Do you want to install kxi-operator version 1.2.3? [Y/n]: 
Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file], input = user_input)
    assert result.exit_code == 0
    assert result.output == expected_output


def test_delete(mocker, k8s):
    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace {test_namespace}
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([HelmCommandDelete()])
    assert delete_crd_params == []


def test_list_versions_default_repo(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'list-versions'])
        expected_output = f"""Listing available kdb Insights Enterprise versions in repo kx-insights
subprocess_stdout
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands(
        [
            HelmCommandRepoUpdate(repo='kx-insights'),
            HelmCommand(helm_cmd=['helm', 'search', 'repo', test_chart])
            ]
        )


def test_list_versions_custom_repo(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'list-versions', '--chart-repo-name', test_chart_repo_name])
        expected_output = f"""Listing available kdb Insights Enterprise versions in repo {test_chart_repo_name}
subprocess_stdout
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands(
        [
            HelmCommandRepoUpdate(repo=test_chart_repo_name),
            HelmCommand(helm_cmd=['helm', 'search', 'repo', test_chart_repo_name + '/insights'])
            ]
        )


def test_delete_specify_release(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    delete_assembly_args = []
    asms_array = [test_asm_name, test_asm_name2]


    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete', '--release', 'atestrelease'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release atestrelease in namespace {test_namespace}
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([HelmCommandDelete(release='atestrelease')])
    assert delete_crd_params == []

def test_delete_specific_release_no_assemblies(mocker, k8s):
    global delete_assembly_args

    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mock_delete_crd(mocker, k8s)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_none)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    delete_assembly_args = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete', '--release', 'atestrelease'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
No assemblies to back up
Uninstalling release atestrelease in namespace {test_namespace}
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == 0
    check_subprocess_run_commands([HelmCommandDelete(release='atestrelease')])
    assert delete_crd_params == []

def test_delete_specific_release_one_assemblies(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    delete_assembly_args = []
    asms_array = [test_asm_name]

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete', '--release', 'atestrelease'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}
Uninstalling release atestrelease in namespace {test_namespace}
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([HelmCommandDelete(release='atestrelease')])
    assert delete_crd_params == []


def test_delete_does_not_prompt_to_remove_operator_and_crd_when_insights_exists(mocker, k8s):
    """
    Tests if a user answers 'n' to removing insights, the kxi exits without further prompts
    """
    global delete_assembly_args

    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    delete_assembly_args = []

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""n
"""
        result = runner.invoke(main.cli, ['install', 'delete'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: n
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == 0
    check_subprocess_run_commands([])
    assert delete_crd_params == []


def test_delete_removes_insights_and_operator(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)
    delete_assembly_args = []
    asms_array = [test_asm_name, test_asm_name2]
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete','--uninstall-operator'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace {test_namespace}
Deleting CRD assemblies.insights.kx.com
Deleting CRD assemblyresources.insights.kx.com
Uninstalling release test-op-helm in namespace kxi-operator
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([
        HelmCommandDelete(),
        HelmCommandDelete(release='test-op-helm',namespace='kxi-operator'),
    ])
    assert delete_crd_params == test_crds


def test_delete_when_insights_and_operator_not_installed(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'delete','--uninstall-operator'])
        expected_output = f"""
kdb Insights Enterprise installation not found
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([])
    assert delete_crd_params == []


def test_delete_error_deleting_crds(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, True)
    utils.mock_kube_crd_api(k8s, delete=utils.raise_not_found)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'delete','--uninstall-operator'])
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: 
Deleting CRD assemblies.insights.kx.com
error=Exception when trying to delete CustomResourceDefinition(assemblies.insights.kx.com): 404
Reason: None
Deleting CRD assemblyresources.insights.kx.com
error=Exception when trying to delete CustomResourceDefinition(assemblyresources.insights.kx.com): 404
Reason: None

kdb Insights Enterprise kxi-operator not found
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([])


def test_delete_removes_insights(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    delete_assembly_args = []
    asms_array = [test_asm_name, test_asm_name2]

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace {test_namespace}
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([HelmCommandDelete()])


def test_delete_force_removes_insights_operator_and_crd(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)

    delete_assembly_args = []
    asms_array = [test_asm_name, test_asm_name2]

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'delete', '--force'])
        expected_output = f"""Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace {test_namespace}
Deleting CRD assemblies.insights.kx.com
Deleting CRD assemblyresources.insights.kx.com
Uninstalling release test-op-helm in namespace kxi-operator
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([
        HelmCommandDelete(),
        HelmCommandDelete(release='test-op-helm',namespace='kxi-operator'),
    ])
    assert delete_crd_params == test_crds


def test_delete_from_given_namespace(mocker, k8s):
    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    global delete_crd_params
    global delete_assembly_args

    delete_assembly_args = []
    delete_crd_params = []
    asms_array = [test_asm_name, test_asm_name2]

    cmd = ['install', 'delete', '--namespace', 'a_test_namespace']
    mocker.patch('sys.argv', cmd)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, cmd, input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace a_test_namespace
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([HelmCommandDelete(namespace='a_test_namespace')])
    assert delete_crd_params == []

def test_delete_given_assembly_backup_filepath(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, False, False)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    global delete_crd_params
    global delete_assembly_args

    delete_assembly_args = []
    delete_crd_params = []
    asms_array = [test_asm_name, test_asm_name2]

    cmd = ['install', 'delete', '--assembly-backup-filepath', 'a_test_file']
    mocker.patch('sys.argv', cmd)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, cmd, input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to a_test_file
Uninstalling release insights in namespace {test_namespace}
"""
        assert os.path.exists('a_test_file')
    assert result.exit_code == 0
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([HelmCommandDelete()])
    assert delete_crd_params == []


def test_delete_when_insights_not_installed(mocker, k8s):
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""n
"""
        result = runner.invoke(main.cli, ['install', 'delete'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise installation not found
"""
    assert result.exit_code == 0
    assert result.output == expected_output
    check_subprocess_run_commands([])
    assert delete_crd_params == []


def test_delete_operator_fails_when_assemblies_running(mocker, k8s):
    global delete_assembly_args

    mock_asm_backup_path(mocker)
    mock_subprocess_run(mocker)
    mock_delete_crd(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mocker.patch(DELETE_ASSEMBLIES_FUNC, mock__delete_assembly)
    delete_assembly_args = []
    asms_array = [test_asm_name, test_asm_name2]
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC, mock_list_assembly_multiple)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
"""
        result = runner.invoke(main.cli, ['install', 'delete','--uninstall-operator'], input=user_input)
        expected_output = f"""
kdb Insights Enterprise is deployed. Do you want to uninstall? [y/N]: y
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name2}'] to {test_asm_backup}
Uninstalling release insights in namespace {test_namespace}
warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
Error: Cannot delete kxi-operator
"""
    assert result.exit_code == 1
    assert result.output == expected_output
    assert len(delete_assembly_args) == len(asms_array)
    for deleted_asm in delete_assembly_args:
        assert deleted_asm['name'] in asms_array
    check_subprocess_run_commands([
        HelmCommandDelete()
    ])
    assert delete_crd_params == []

def test_install_when_not_deploying_keycloak(mocker, k8s):
    setup_mocks(mocker, k8s)
    with temp_test_output_file() as test_output_file, temp_config_file() as test_cli_config:
        shutil.copyfile(expected_test_output_file, test_output_file)
        # Ideally would patch sys.argv with args but can't find a way to get this to stick
        #   'mocker.patch('sys.argv', args)'
        # doesn't seem to be persist into the runner.invoke
        mocker.patch('kxicli.commands.install.deploy_keycloak', lambda: False)

        cmd = ['install', 'setup', '--keycloak-auth-url', test_auth_url, '--output-file', test_output_file]
        test_cfg = {
            'values_exist': True,
            'overwrite_values': 'y',
            'deploy_keycloak': False
        }
        run_cli(cmd, test_cfg, test_cli_config, test_output_file, 0)
        assert compare_files(test_output_file, test_val_file_shared_keycloak)


def test_get_values_returns_error_when_does_not_exist(mocker, k8s):
    runner = CliRunner()

    helm_error = 'Error: release: not found'
    utils.mock_helm_get_values(mocker, utils.test_val_data, True, helm_error)

    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'get-values', '--namespace', 'missing'])

    assert result.exit_code == 1
    assert result.output == f"Error: {phrases.helm_get_values_fail.format(release='insights', namespace='missing', helm_error=helm_error)}\n"


def test_get_values_returns_decoded_secret(mocker, k8s):
    data = {'a': 1}
    utils.mock_helm_get_values(mocker, data)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'get-values'])

    assert result.exit_code == 0
    # trim off trailing \n added by click.echo
    assert result.output[:-1] == yaml.safe_dump(data)

def test_upgrade(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    with open(utils.test_asm_file) as f:
        file = yaml.safe_load(f)
        last_applied = file['metadata']['annotations'][CONFIG_ANNOTATION]
        test_asm_file_contents = json.loads(last_applied)
    with open(utils.test_val_file, 'r') as values_file:
        values = str(values_file.read())
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Installing chart kx-insights/kxi-operator version 1.2.3 with previously used values
Replacing CRD assemblies.insights.kx.com
Replacing CRD assemblyresources.insights.kx.com
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with previously used values

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete
kxi-management-service is already installed with version 0.1.2
Would you like to upgrade to version {test_management_version}? [y/N]: y

Upgrading KXI Management Service
Installing chart kx-insights/kxi-management-service version {test_management_version} with previously used values

Upgrade to version {test_management_version} complete
"""
    install_upgrade_checks(result)
    assert result.output == expected_output


def test_upgrade_management_service(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    with open(utils.test_asm_file) as f:
        file = yaml.safe_load(f)
        last_applied = file['metadata']['annotations'][CONFIG_ANNOTATION]
        test_asm_file_contents = json.loads(last_applied)
    with open(utils.test_val_file, 'r') as values_file:
        values = str(values_file.read())
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Installing chart kx-insights/kxi-operator version 1.2.3 with previously used values
Replacing CRD assemblies.insights.kx.com
Replacing CRD assemblyresources.insights.kx.com
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with previously used values

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete
kxi-management-service is already installed with version 0.1.2
Would you like to upgrade to version {test_management_version}? [y/N]: y

Upgrading KXI Management Service
Installing chart kx-insights/kxi-management-service version {test_management_version} with previously used values

Upgrade to version {test_management_version} complete
"""
    install_upgrade_checks(result)
    assert result.output == expected_output
    
def test_upgrade_import_users(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    with open(utils.test_asm_file) as f:
        file = yaml.safe_load(f)
        last_applied = file['metadata']['annotations'][CONFIG_ANNOTATION]
        test_asm_file_contents = json.loads(last_applied)
    with open(utils.test_val_file, 'r') as values_file:
        values = str(values_file.read())
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup, '--import-users', 'True'],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Installing chart kx-insights/kxi-operator version 1.2.3 with previously used values
Replacing CRD assemblies.insights.kx.com
Replacing CRD assemblyresources.insights.kx.com
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with previously used values

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete
kxi-management-service is already installed with version 0.1.2
Would you like to upgrade to version {test_management_version}? [y/N]: y

Upgrading KXI Management Service
Installing chart kx-insights/kxi-management-service version {test_management_version} with previously used values

Upgrade to version {test_management_version} complete
"""
    expected_helm_commands=[
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(values = '-', release = test_operator_helm_name),
        HelmCommandInsightsInstall(values = '-', keycloak_importUsers= 'true'),
        HelmCommandManagementInstall(values = '-')
    ]
    install_upgrade_checks(result, helm_commands=expected_helm_commands)
    assert result.output == expected_output


def test_upgrade_without_backup_filepath(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)    
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    with open(utils.test_val_file, 'r') as values_file:
        values = str(values_file.read())
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3'],
            input=user_input
        )
    install_upgrade_checks(result)



#     upgrades_mocks(mocker, k8s)
#     mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
#     mock_get_operator_version(mocker)
#     mock_get_management_version(mocker)
#     mock_copy_secret(mocker, k8s)
#     utils.mock_validate_secret(mocker)
#     mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
#     runner = CliRunner()
#     user_input = f"""y
# y
# """
#     with runner.isolated_filesystem():
#         result = runner.invoke(main.cli,
#             ['install', 'upgrade', '--version', '1.2.3', '--filepath', utils.test_val_file],
#             input=user_input
#         )
#     expected_output = f"""{phrases.header_upgrade}
# {phrases.values_validating}

# Do you want to install kxi-operator version 1.2.3? [Y/n]: y
# kdb Insights Enterprise is not deployed. Skipping to install
# Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
# Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

# Upgrade to version 1.2.3 complete
# """
#     assert result.exit_code == 0
#     assert result.output == expected_output
#     check_subprocess_run_commands([
#         HelmCommandRepoUpdate(),
#         HelmCommandOperatorInstall()
#     ])



def test_upgrade_skips_to_install_when_not_running_but_fails_with_no_values_file(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_get_values(mocker, None, True, 'Command returned non-zero exit status 1.')
    runner = CliRunner()
    user_input = f"""y
"""
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3'],
            input=user_input
        )
    expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

Do you want to install kxi-operator version 1.2.3? [Y/n]: y
kdb Insights Enterprise is not deployed. Skipping to install
Error: {phrases.values_filepath_missing}
"""
    assert result.exit_code == 1
    assert result.output == expected_output


def test_upgrade_when_user_declines_to_teardown_assembly(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_multiple)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    utils.mock_validate_secret(mocker)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)

    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--filepath', utils.test_val_file,
                '--assembly-backup-filepath', test_asm_backup],
            input='y\ny\nn\n'
        )
    expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}', '{test_asm_name + '2'}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down
Tearing down assembly {test_asm_name2}
Are you sure you want to teardown {test_asm_name2} [y/N]: n
Not tearing down assembly {test_asm_name2}

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!
Submitting assembly {test_asm_name2}
Custom assembly resource {test_asm_name2} created!

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    install_upgrade_checks(result,
                           helm_commands=[HelmCommandRepoUpdate(), HelmCommandManagementInstall()],
                           docker_config_check=False,
                           expected_subprocess_args=[True, None, None],
                           expected_delete_crd_params=[],
                           expected_running_assembly={test_asm_name:True, test_asm_name2:True},
    )
    assert result.output == expected_output


def test_upgrade_does_not_reapply_assemblies_when_upgrade_fails(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    with open(utils.test_asm_file) as f:
        file = yaml.safe_load(f)
        last_applied = file['metadata']['annotations'][CONFIG_ANNOTATION]
        test_asm_file_contents = json.loads(last_applied)
    def mocked_failed_upgrade(*args, **kwargs):
        if args[0][:2] == ['helm', 'upgrade']:
            raise subprocess.CalledProcessError(1, args[0], stderr=b'Deployment failed \x143\x071\x142\x067')
    mocker.patch('subprocess.run', mocked_failed_upgrade)
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Installing chart kx-insights/kxi-operator version 1.2.3 with previously used values
Error: Command "helm upgrade --install --version 1.2.3 -f - test-op-helm kx-insights/kxi-operator --namespace kxi-operator" failed with output:
  Deployment failed \x143\x071\x142\x067
"""
    assert result.exit_code == 1
    assert result.output == expected_output
    with open(test_asm_backup) as f:
        expect = yaml.safe_load(f)
        assert expect == {'items': [test_asm_file_contents]}
    assert insights_installed_flag == True
    assert operator_installed_flag == True
    assert crd_exists_flag == True
    assert os.path.isfile(test_asm_backup)
    os.remove(test_asm_backup)

def test_install_run_upgrades_when_already_installed(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    mocker.patch('kxicli.commands.assembly._backup_filepath', lambda filepath, force: test_asm_backup)
    runner = CliRunner()
    user_input = f"""y
y
y
"""
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file], input =user_input)
        expected_output = f"""{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
Would you like to upgrade to version 1.2.3? [y/N]: y
kxi-operator already installed with version 1.2.0
Do you want to install kxi-operator version 1.2.3? [Y/n]: y
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Replacing CRD assemblies.insights.kx.com
Replacing CRD assemblyresources.insights.kx.com
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete

Installing kxi-management-service to version {test_management_version}
Installing chart kx-insights/kxi-management-service version 0.1.3 with values file from {utils.test_val_file}

Install complete for the KXI Management Service
"""
    expected_helm_commands=[
        HelmCommandRepoUpdate(),
        HelmCommandOperatorInstall(values=utils.test_val_file, release=test_operator_helm_name),
        HelmCommandInsightsInstall(values=utils.test_val_file, keycloak_importUsers= 'false'),
        HelmCommandManagementInstall(values=utils.test_val_file)
    ]
    install_upgrade_checks(result,
                           helm_commands=expected_helm_commands,
                           expected_subprocess_args=[True, None, None],
                           )
    assert result.output == expected_output


def test_upgrade_without_op_name_prompts_to_skip_operator_install(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    with open(utils.test_asm_file) as f:
        file = yaml.safe_load(f)
        last_applied = file['metadata']['annotations'][CONFIG_ANNOTATION]
        test_asm_file_contents = json.loads(last_applied)
    with open(utils.test_val_file, 'r') as values_file:
        values = str(values_file.read())
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
warn=kxi-operator already installed, but not managed by helm
Not installing kxi-operator

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with previously used values

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete
kxi-management-service is already installed with version 0.1.2
Would you like to upgrade to version {test_management_version}? [y/N]: y

Upgrading KXI Management Service
Installing chart kx-insights/kxi-management-service version {test_management_version} with previously used values

Upgrade to version {test_management_version} complete
"""
    install_upgrade_checks(result,
                           helm_commands=[
                               HelmCommandRepoUpdate(),
                               HelmCommandInsightsInstall(values = '-',
                                                          keycloak_importUsers='false'
                                                          ),
                               HelmCommandManagementInstall(values = '-')
                           ],
                           expected_delete_crd_params=[]
    )
    assert result.output == expected_output



def test_upgrade_prompts_to_skip_operator_install_when_assemblies_running(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    mock_copy_secret(mocker, k8s)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC, mock_list_assembly_multiple)
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = f"""y
y
y
y
y
"""
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
warn=Cannot upgrade kxi-operator
Do you want continue to upgrade kdb Insights Enterprise without upgrading kxi-operator? [Y/n]: y

Backing up assemblies
Persisted assembly definitions for ['{test_asm_name}'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-upgrade
Tearing down assembly {test_asm_name}
Are you sure you want to teardown {test_asm_name} [y/N]: y
Waiting for assembly to be torn down

Upgrading insights
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.3.tgz
Installing chart kx-insights/insights version 1.2.3 with previously used values

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly {test_asm_name}
Custom assembly resource {test_asm_name} created!

Upgrade to version 1.2.3 complete
kxi-management-service is already installed with version 0.1.2
Would you like to upgrade to version {test_management_version}? [y/N]: y

Upgrading KXI Management Service
Installing chart kx-insights/kxi-management-service version {test_management_version} with previously used values

Upgrade to version {test_management_version} complete
"""
    install_upgrade_checks(result,
                           helm_commands=[
                               HelmCommandRepoUpdate(),
                               HelmCommandInsightsInstall(values = '-',
                                                          keycloak_importUsers='false'
                                                          ),
                               HelmCommandManagementInstall(values = '-')
                           ],
                           expected_delete_crd_params=[]
    )
    assert result.output == expected_output

def test_upgrade_exits_when_user_does_not_proceed_when_assemblies_running(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    if os.path.exists(test_asm_backup):
        os.remove(test_asm_backup)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC, mock_list_assembly_multiple)
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        user_input = "n"
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.0
warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
warn=Cannot upgrade kxi-operator
Do you want continue to upgrade kdb Insights Enterprise without upgrading kxi-operator? [Y/n]: n
Error: Cannot upgrade kxi-operator
"""
    assert result.exit_code == 1
    assert result.output == expected_output


def test_upgrade_with_no_assemblies(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    utils.mock_helm_get_values(mocker, utils.test_val_data)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_helm_list_returns_valid_json_management)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_none)
    global running_assembly
    running_assembly = {}
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli,
            ['install', 'upgrade', '--version', '1.2.3', '--assembly-backup-filepath', test_asm_backup],
            input="y\ny"
        )
    install_upgrade_checks(result, expected_running_assembly={})


def test_install_upgrade_errors_when_repo_does_not_exist(mocker, k8s):
    # return an empty repo list, then a populated one after the repo has been added
    helm_list = [
        [],
        [{'name': test_chart_repo, 'url': test_chart_repo_url}]
    ]
    mocker.patch('kxicli.resources.helm.repo_list', side_effect=helm_list)
    mocker.patch('kxicli.resources.helm.add_repo', mocked_helm_add_repo)
    mocker.patch('kxicli.resources.helm.repo_update')
    mock_subprocess_run(mocker)
    mock_copy_secret(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, False, False, False)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_secret_api(k8s, read=mocked_read_secret)
    runner = CliRunner()
    user_input = f"""
{test_user}
{test_pass}
{test_pass}
"""

    expected_output = f"""Upgrading kdb Insights Enterprise
{phrases.values_validating}

Please enter the chart repository URL to pull charts from [https://nexus.dl.kx.com/repository/kx-insights-charts]: 
Please enter the username for the chart repository: {test_user}
Please enter the password for the chart repository (input hidden): 
Re-enter to confirm (input hidden): 
Do you want to install kxi-operator version 1.2.3? [Y/n]: 
kdb Insights Enterprise is not deployed. Skipping to install
Installing chart kx-insights/kxi-operator version 1.2.3 with values file from {utils.test_val_file}
Installing chart kx-insights/insights version 1.2.3 with values file from {utils.test_val_file}

Upgrade to version 1.2.3 complete
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'upgrade', '--version', '1.2.3', '--filepath', utils.test_val_file], input=user_input)
    assert result.exit_code == 0
    assert result.output == expected_output


def test_install_upgrade_errors_when_downgrading_to_lower_version(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    utils.mock_validate_secret(mocker)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'upgrade', '--version', '1.0.0', '--filepath', utils.test_val_file])
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
Error: Cannot upgrade from version 1.2.1 to version 1.0.0. Target version must be higher than currently installed version.
"""
    assert result.exit_code == 1
    assert result.output == expected_output


def test_install_run_errors_when_downgrading_to_lower_version(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    utils.mock_validate_secret(mocker)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'run', '--version', '1.0.0', '--filepath', utils.test_val_file])
        expected_output = f"""{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
Error: Cannot upgrade from version 1.2.1 to version 1.0.0. Target version must be higher than currently installed version.
"""
    assert result.exit_code == 1
    assert result.output == expected_output


def test_install_upgrade_errors_when_downgrading_operator_to_lower_version(mocker, k8s):
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions',
                 lambda x:((['1.2.2'], [test_operator_helm_name])))
    utils.mock_validate_secret(mocker)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main.cli, ['install', 'upgrade', '--filepath', utils.test_val_file,
                                          '--version', '1.2.3', '--operator-version', '1.2.0'])
        expected_output = f"""{phrases.header_upgrade}
{phrases.values_validating}

kdb Insights Enterprise is already installed with version 1.2.1
kxi-operator already installed with version 1.2.2
Error: Cannot upgrade from version 1.2.2 to version 1.2.0. Target version must be higher than currently installed version.
"""
    assert result.exit_code == 1
    assert result.output == expected_output


def test_install_values_validated_on_run_and_upgrade(mocker, k8s):
    utils.mock_helm_repo_list(mocker)
    mocker.patch('kxicli.resources.helm.repo_update')
    test_cfg = {
        'lic_sec_exists': False,
        'lic_sec_is_valid': False,
        'image_sec_exists': True,
        'image_sec_is_valid': False,
        'client_sec_exists': True,
        'client_sec_is_valid': False,
        'kc_secret_exists': True,
        'kc_secret_is_valid': False,
        'pg_secret_exists': True,
        'pg_secret_is_valid': False
    }
    utils.mock_validate_secret(mocker, exists=False)
    cmd = ['install', 'upgrade', '--version', '1.2.3', '--filepath', utils.test_val_file]
    run_cli(cmd, test_cfg, expected_exit_code = 1)
    cmd = ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file]
    run_cli(cmd, test_cfg, expected_exit_code = 1)

    utils.mock_validate_secret(mocker, is_valid=False)
    test_cfg['lic_sec_exists'] = True
    cmd = ['install', 'upgrade', '--version', '1.2.3', '--filepath', utils.test_val_file]
    run_cli(cmd, test_cfg, expected_exit_code = 1)
    cmd = ['install', 'run', '--version', '1.2.3', '--filepath', utils.test_val_file]
    run_cli(cmd, test_cfg, expected_exit_code = 1)


def mock_helm_list_history_same_operator(mocker, release='kx-insights', output=None):
    mocker.patch('kxicli.resources.helm.history', utils.mocked_helm_history_rollback_same_operator)

def mock_helm_list_history(mocker, release='kx-insights', output=None):
    mocker.patch('kxicli.resources.helm.history', utils.mocked_helm_history_rollback)

def mock_helm_list_histor_broken(mocker, release='kx-insights', output=None):
    mocker.patch('kxicli.resources.helm.history', utils.mocked_helm_history_rollback_broken)

def test_install_rollback(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    mock_helm_list_history_same_operator(mocker)
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release_14)
    user_input = f"""y
    y
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '--namespace', test_namespace, '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
    expected_output = f"""Rolling Insights back to version 1.4.1 and revision 1. Operator version remaining on 1.4.0.
Proceed? [y/N]: y

Backing up assemblies
Persisted assembly definitions for ['basic-assembly'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-rollback
Tearing down assembly basic-assembly
Are you sure you want to teardown basic-assembly [y/N]:     y
Waiting for assembly to be torn down
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.1.tgz

Rolling back Insights
Rollback kdb Insights Enterprise complete for version 1.4.1

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly basic-assembly\nCustom assembly resource basic-assembly created!
"""
    assert result.exit_code == 0
    assert expected_output == result.output

def test_install_rollback_fail_version(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    mock_helm_list_history(mocker)
    expected_output = "Error: Insights rollback target version 1.2.3 is incompatible with target operator version 1.4.0. Minor versions must match.\n"
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    utils.mock_helm_env(mocker)
    utils.mock_helm_fetch(mocker)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release_14)

    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '--namespace', test_namespace]
        )
    assert result.exit_code == 1
    assert expected_output ==  result.output

def test_install_rollback_revision(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    mock_helm_list_history(mocker)
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    user_input = f"""y
    y
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '1', '--operator-revision', '1', '--namespace', test_namespace, '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
    expected_output = f"""Rolling Insights back to version 1.2.3 and revision 1.
Rolling operator back to version 1.2.3 and revision 1.
Proceed? [y/N]: y

Backing up assemblies
Persisted assembly definitions for ['basic-assembly'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-rollback
Tearing down assembly basic-assembly
Are you sure you want to teardown basic-assembly [y/N]:     y
Waiting for assembly to be torn down
Rollback kxi-operator complete for version 1.2.3
Using image.pullSecret from embedded default values: kxi-nexus-pull-secret
Reading CRD data from {utils.test_helm_repo_cache}/kxi-operator-1.2.3.tgz
Replacing CRD assemblies.insights.kx.com
Replacing CRD assemblyresources.insights.kx.com
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.1.tgz

Rolling back Insights
Rollback kdb Insights Enterprise complete for version 1.2.3

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly basic-assembly\nCustom assembly resource basic-assembly created!
"""
    assert result.exit_code == 0
    assert expected_output == result.output

def test_install_rollback_insights_revision_fail(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    utils.mock_helm_env(mocker)
    expected_output = 'Error: Could not find revision 4 in history\n'
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release_14)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_none)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)
    mock_helm_list_history(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_repo_list(mocker)
    mocker.patch('kxicli.commands.install.helm.repo_update')
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '4', '--namespace', test_namespace]
        )
    assert result.exit_code == 1
    assert expected_output == result.output

def test_install_rollback_operator_revision_fail(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release_14)
    expected_output = 'Error: Could not find revision 4 in kxi-operator history\n'
    mock_helm_list_history(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_repo_list(mocker)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_none)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)
    mocker.patch('kxicli.commands.install.helm.repo_update')
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '--operator-revision', '4']
        )
    assert result.exit_code == 1
    assert expected_output == result.output

def test_install_rollback_operator_revision_fail_with_insightsrevision(mocker, k8s):
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release_14)
    mocker.patch('subprocess.check_output',return_value="")
    expected_output = 'Error: Could not find revision 4 in kxi-operator history\n'
    mock_helm_list_history(mocker)
    utils.mock_helm_env(mocker)
    utils.mock_helm_repo_list(mocker)
    mocker.patch(GET_ASSEMBLIES_LIST_FUNC, mock_list_assembly_none)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC)
    mocker.patch('kxicli.commands.install.helm.repo_update')
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '1','--operator-revision', '4']
        )
    assert result.exit_code == 1
    assert expected_output == result.output

def test_install_rollback_skips_operator_when_assemblies_running(mocker, k8s):
    mocker.patch('subprocess.check_output',return_value="")
    mock_helm_list_history(mocker)
    upgrades_mocks(mocker, k8s)
    mock_set_insights_operator_and_crd_installed_state(mocker, True, True, True)
    mock_get_operator_version(mocker)
    mock_get_management_version(mocker)
    utils.mock_validate_secret(mocker)
    utils.mock_kube_crd_api(k8s, create=mocked_create_crd, delete=mocked_delete_crd)
    utils.mock_helm_env(mocker)
    mocker.patch('kxicli.commands.install.read_cached_crd_files', mock_read_cached_crd_data)
    utils.mock_helm_fetch(mocker)
    mocker.patch(LIST_CLUSTER_ASSEMBLIES_FUNC, mock_list_assembly_multiple)
    user_input = f"""y
    y
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # these are responses to the various prompts
        result = runner.invoke(main.cli,
            ['install', 'rollback', '1', '--operator-revision', '1', '--namespace', test_namespace, '--assembly-backup-filepath', test_asm_backup],
            input=user_input
        )
    expected_output = f"""warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
warn=Cannot rollback kxi-operator
Rolling Insights back to version 1.2.3 and revision 1. Operator version remaining on 1.2.0.
Proceed? [y/N]: y

Backing up assemblies
Persisted assembly definitions for ['basic-assembly'] to {test_asm_backup}

Tearing down assemblies
Assembly data will be persisted and state will be recovered post-rollback
Tearing down assembly basic-assembly
Are you sure you want to teardown basic-assembly [y/N]:     y
Waiting for assembly to be torn down
Reading upgrade data from {utils.test_helm_repo_cache}/insights-1.2.1.tgz

Rolling back Insights
Rollback kdb Insights Enterprise complete for version 1.2.3

Reapplying assemblies
Submitting assembly from {test_asm_backup}
Submitting assembly basic-assembly\nCustom assembly resource basic-assembly created!
"""
    assert result.exit_code == 0
    assert expected_output == result.output
