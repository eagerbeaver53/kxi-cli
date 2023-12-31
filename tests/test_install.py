"""This install test is meant to unit test the individual functions in the install command"""
import base64
import copy
import io
import json
import pyk8s
import pytest
import click
from pathlib import Path

from kxicli import common, phrases
from kxicli.commands import install
from kxicli.resources import helm_chart
import mocks
from utils import IPATH_KUBE_COREV1API, temp_file, test_secret_data, test_secret_type, test_secret_key, \
    mock_kube_deployment_api, mocked_kube_deployment_list, mock_kube_secret_api, mocked_read_namespaced_secret, \
    raise_conflict, raise_not_found, mock_validate_secret, mock_helm_env, mock_helm_get_values,  mock_helm_repo_list, \
    return_none, fake_docker_config_yaml, test_val_file, fake_secret
# need to replace the above imports with utils prefixed versions
import utils
from test_install_e2e import test_vals, mocked_read_secret, mocked_installed_chart_json, \
    mock_list_assembly_multiple, LIST_CLUSTER_ASSEMBLIES_FUNC
from const import test_user, test_pass, test_lic_file, test_chart_repo_name, test_chart_repo_url, insights_tgz

# Common test parameters
test_ns = 'test-ns'
test_repo = 'test.kx.com'
test_secret = 'test-secret'
test_key = install.gen_private_key()
test_cert = install.gen_cert(test_key)
test_values_yaml = 'values.yaml'
test_values_keys = ('values.yaml',)

common.config.load_config("default")

# Constants for common import paths
SYS_STDIN = 'sys.stdin'

fun_subprocess_check_output = 'subprocess.check_output'


def populate(secret, **kwargs):
    secret.data = kwargs.get('data')
    return secret


# These are used to mock helm calls to list deployed releases
# helm list --filter insights --deployed -o json
def mocked_helm_list_returns_valid_json(base_command):
    return '[{"name":"insights","namespace":"testNamespace","revision":"1","updated":"2022-02-23 10:39:53.7668809 +0000 UTC","status":"deployed","chart":"insights-0.11.0-rc.39","app_version":"0.11.0-rc.8"}]'


def mocked_helm_list_returns_empty_json(base_command):
    return '[]'


def mocked_helm_search_returns_valid_json(base_command, check=True, capture_output=True, text=True):
    return install.subprocess.CompletedProcess(
        args=base_command,
        returncode=0,
        stdout='[{"name":"kx-insights/kxi-operator","version":"1.3.0","app_version":"1.3.0","description":"KX Insights Operator"}]\n'
    )


def mocked_helm_search_returns_valid_json_rc(base_command, check=True, capture_output=True, text=True):
    return install.subprocess.CompletedProcess(
        args=base_command,
        returncode=0,
        stdout='[{"name":"kx-insights/kxi-operator","version":"1.3.0-rc.40","app_version":"1.3.0","description":"KX Insights Operator"}]\n'
    )

def mocked_helm_search_returns_valid_json_optional_multiple_versions(base_command, check=True, capture_output=True, text=True):
    return install.subprocess.CompletedProcess(
        args=base_command,
        returncode=0,
        stdout='[{"name":"kx-insights/kxi-operator","version":"1.3.0-rc.32","app_version":"1.3.0","description":"KX Insights Operator"}, {"name":"kx-insights/kxi-operator","version":"1.3.1-rc.1","app_version":"1.3.1-rc.1","description":"KX Insights Operator"}]\n'
    )

def mocked_helm_search_returns_empty_json(base_command, check=True, capture_output=True, text=True):
    return install.subprocess.CompletedProcess(
        args=base_command,
        returncode=0,
        stdout='[]\n'
    )


def test_create_docker_config():
    test_cfg = {
        'auths': {
            test_repo: {
                'username': test_user,
                'password': test_pass,
                'auth': base64.b64encode(f'{test_user}:{test_pass}'.encode()).decode('ascii')
            }
        }
    }

    assert install.create_docker_config(test_repo, test_user, test_pass) == test_cfg


def test_create_docker_secret(mocker, k8s):
    mock_kube_secret_api(k8s)

    test_cfg = install.create_docker_config(test_repo, test_user, test_pass)

    s = fake_secret(test_ns, test_secret, install.SECRET_TYPE_DOCKERCONFIG_JSON, install.IMAGE_PULL_KEYS)
    res = install.populate_docker_config_secret(s, test_cfg)

    assert res.type == 'kubernetes.io/dockerconfigjson'
    assert res.metadata.name == test_secret
    assert '.dockerconfigjson' in res.data


def test_get_docker_config_secret(mocker, k8s):
    mock_kube_secret_api(k8s, read=mocked_read_secret)
    assert install.get_docker_config_secret(
        namespace='test-namespace',
        secret_name=common.get_default_val('image.pullSecret')

    ) == fake_docker_config_yaml


def test_get_docker_config_secret_fail(mocker, k8s):
    mock_kube_secret_api(k8s, read=return_none)
    with pytest.raises(click.ClickException):
        install.get_docker_config_secret(
        namespace='test-namespace',
        secret_name=common.get_default_val('image.pullSecret')
        )

def test_create_license_secret_encoded(mocker, k8s):
    mock_kube_secret_api(k8s)

    s = fake_secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.LICENSE_KEYS)
    s, _ = install.populate_license_secret(s, test_lic_file, True)
    res = s

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'license' in res.stringData
    with open(test_lic_file, 'rb') as license_file:
        assert base64.b64decode(res.stringData['license']) == license_file.read()


def test_create_license_secret_decoded(k8s):
    mock_kube_secret_api(k8s)

    s = fake_secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.LICENSE_KEYS)
    s, _ = install.populate_license_secret(s, test_lic_file, False)
    res = s

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'license' in res.data
    with open(test_lic_file, 'rb') as license_file:
        assert base64.b64decode(res.data['license']) == license_file.read()


def test_create_tls_secret(k8s):
    mock_kube_secret_api(k8s)

    s = fake_secret(test_ns, test_secret, install.SECRET_TYPE_TLS)
    s = install.populate_tls_secret(s, test_cert, test_key)
    res = s

    assert res.type == 'kubernetes.io/tls'
    assert res.metadata.name == test_secret
    assert 'tls.crt' in res.data
    assert 'tls.key' in res.data


def test_create_keycloak_secret_from_cli_config(k8s):
    mock_kube_secret_api(k8s)
    admin_pass = 'test-keycloak-admin-password'
    management_pass = 'test-keycloak-management-password'
    common.config.config['default']['keycloak.admin.password'] = admin_pass
    common.config.config['default']['keycloak.management.password'] = management_pass

    s = fake_secret(test_ns, test_secret)
    s = install.populate_keycloak_secret(s)
    res = s

    assert res.type == 'Opaque'
    assert res.metadata.name == test_secret
    assert 'admin-password' in res.data
    assert 'management-password' in res.data
    assert base64.b64decode(res.data['admin-password']).decode('ascii') == admin_pass
    assert base64.b64decode(res.data['management-password']).decode('ascii') == management_pass
    common.config.load_config('default')


def test_create_postgres_secret_from_cli_config(k8s):
    mock_kube_secret_api(k8s)
    postgres_pass = 'test-postgres-admin-password'
    user_pass = 'test-postgres-user-password'
    common.config.config['default']['postgresql.postgres.password'] = postgres_pass
    common.config.config['default']['postgresql.user.password'] = user_pass

    s = fake_secret(test_ns, test_secret)
    s = install.populate_postgresql_secret(s)
    res = s

    assert res.type == 'Opaque'
    assert res.metadata.name == test_secret
    assert 'postgresql-postgres-password' in res.data
    assert 'postgres-password' in res.data
    assert 'postgresql-password' in res.data
    assert 'password' in res.data

    assert base64.b64decode(res.data['postgresql-postgres-password']).decode('ascii') == postgres_pass
    assert base64.b64decode(res.data['postgres-password']).decode('ascii') == postgres_pass
    assert base64.b64decode(res.data['postgresql-password']).decode('ascii') == user_pass
    assert base64.b64decode(res.data['password']).decode('ascii') == user_pass
    common.config.load_config('default')


def test_copy_secret(mocker, k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)
    assert install.copy_secret(test_secret, test_ns, 'to_ns') == None


def test_copy_secret_conflict(mocker, k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret, create=raise_conflict)
    assert install.copy_secret(test_secret, test_ns, 'to_ns') == None


def test_get_secret_returns_decoded_secret(k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)

    s = fake_secret(test_ns, test_secret)
    res = install.get_secret(s, test_secret_key)

    assert res == base64.b64decode(test_secret_data[test_secret_key]).decode('ascii')


def test_get_secret_when_does_not_exist(k8s):
    mock_kube_secret_api(k8s)
    s = fake_secret(test_ns, test_secret)
    res = install.get_secret(s, test_values_yaml)

    assert res == None
    
def test_get_secret_keyerror(k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)
    s = fake_secret(test_ns, test_secret)
    res = install.get_secret(s, "doesnotexists")

    assert res == None


def test_get_operator_version_returns_operator_version_if_passed_regardless_of_rc():
    non_rc = install.get_operator_version('kxi-insights', '1.2.3', '4.5.6')
    rc = install.get_operator_version('kxi-insights', '1.2.3-rc.1', '4.5.6')

    assert non_rc == '4.5.6'
    assert rc == '4.5.6'

def get_minor_version_returns_minor_version_from_semver():
    assert install.get_minor_version('1.0.0') == '1.0'
    assert install.get_minor_version('1.2.3') == '1.2'
    assert install.get_minor_version('1.2.3-rc.50') == '1.2'

def mocked_subprocess_get_operator_version(chart_repo_name, insights_version, rc_version):
        if rc_version:
            return f'{insights_version}-rc.40'
        else:
            return insights_version

def mocked_subprocess_get_operator_version_inc(chart_repo_name, insights_version, rc_version):
        return '1.5.0'

def mocked_subprocess_get_operator_version_none(chart_repo_name, insights_version, rc_version):
        return ''

def test_get_operator_version_returns_latest_minor_version(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    assert install.get_operator_version(chart, '1.3.0', None) == '1.3.0'

def test_get_operator_version_returns_latest_minor_version_multiple_versions(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json_optional_multiple_versions)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    assert install.get_operator_version(chart, '1.3.0', None) is None

def test_get_operator_version_returns_latest_minor_version_rc(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json_rc)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    assert install.get_operator_version(chart, '1.3.0-rc.30', None) == '1.3.0-rc.40'

def test_get_operator_version_returns_none_when_not_found(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    assert install.get_operator_version(chart, '5.6.7', None) == None


def test_get_installed_charts_returns_chart_json(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_valid_json)
    assert install.get_installed_charts('insights', test_ns) == json.loads(mocked_helm_list_returns_valid_json(''))


def test_insights_installed_returns_true_when_already_exists(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_valid_json)
    assert install.insights_installed('insights', test_ns) == True


def test_insights_installed_returns_false_when_does_not_exist(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_empty_json)
    assert install.insights_installed('insights', test_ns) == False


def test_get_installed_operator_versions_returns_helm_chart_version(k8s):
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    assert install.get_installed_operator_versions('kx-operator') == (['1.2.3'], ['test-helm-name'])


def test_get_installed_operator_versions_returns_helm_chart_version_when_does_not_exist(k8s):
    mock_kube_deployment_api(k8s)
    assert install.get_installed_operator_versions('kx-operator') == ([], [])


def test_sanitize_auth_url():
    https_replaced = install.sanitize_auth_url('https://keycloak.keycloak.svc.cluster.local/auth/')
    trailing_slash = install.sanitize_auth_url('https://keycloak.keycloak.svc.cluster.local/auth')
    prepend_http = install.sanitize_auth_url('keycloak.keycloak.svc.cluster.local/auth')

    expected = 'http://keycloak.keycloak.svc.cluster.local/auth/'
    assert https_replaced == expected
    assert trailing_slash == expected
    assert prepend_http == expected


def test_get_image_and_license_secret_from_values_returns_defaults():
    assert install.get_image_and_license_secret_from_values({}, None, None) == (
    'kxi-nexus-pull-secret', 'kxi-license')

def test_get_image_and_license_secret_from_values_args_overrides_values_dict():
    assert install.get_image_and_license_secret_from_values(test_vals,
                                                            'image-pull-from-arg',
                                                            'license-from-arg') == (
           'image-pull-from-arg', 'license-from-arg')

def test_get_image_and_license_secret_returns_error_when_invalid_dict_passed():
    with pytest.raises(Exception) as e:
        install.get_image_and_license_secret_from_values(test_lic_file, None, None)
    assert isinstance(e.value, click.ClickException)
    assert f'Invalid values' in e.value.message


def test_ensure_secret_when_does_not_exist(k8s):
    mock_kube_secret_api(k8s)

    key = 'a'
    secret_data = {key: '1'}
    s = fake_secret(test_ns, test_secret, test_secret_type)
    res = install.ensure_secret(s, populate, data=secret_data)

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert key in res.data
    assert res.data[key] == '1'


def test_ensure_secret_when_secret_exists_and_is_valid(k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)
    s = fake_secret(test_ns, test_secret)
    res = install.ensure_secret(s, populate, data=test_secret_data)

    assert res.type == "Opaque"
    assert res.metadata.name == test_secret
    # populate function should not be called to update the data because it's already valid
    assert res.data == {}

def test_ensure_secret_when_secret_exists_but_is_invalid_w_overwrite(mocker, monkeypatch, k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)
    mock_validate_secret(mocker, is_valid=False)

    # patch stdin to 'n' for the prompt rejecting secret overwrite
    monkeypatch.setattr(SYS_STDIN, io.StringIO('y'))

    new_key = 'xyz'
    new_data = {new_key: '123'}

    s = fake_secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = install.ensure_secret(s, populate, data=new_data)

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert new_key in res.data
    assert res.data[new_key] == new_data[new_key]


def test_ensure_secret_when_secret_exists_but_is_invalid_w_no_overwrite(mocker, monkeypatch, k8s):
    mock_kube_secret_api(k8s, read=mocked_read_namespaced_secret)
    mock_validate_secret(mocker, is_valid=False)

    # patch stdin to 'n' for the prompt rejecting secret overwrite
    monkeypatch.setattr(SYS_STDIN, io.StringIO('n'))

    s = fake_secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = install.ensure_secret(s, populate, data={'a': 1})

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert test_secret_key in res.data
    assert res.data[test_secret_key] == test_secret_data[test_secret_key]


def test_read_cache_crd_from_file_throws_yaml_error(mocker):
    mock_helm_env(mocker)

    # mock data returned from tar extraction
    mocker.patch('kxicli.common.extract_files_from_tar', return_value=['abc: 123\n    def: 456'])
    with pytest.raises(Exception) as e:
        install.read_cached_crd_files(
            '1.2.3',
            Path(utils.test_helm_repo_cache),
            'kxi-operator',
            [install.CRD_FILES[0]]
            )

    assert isinstance(e.value, click.ClickException)
    assert 'Failed to parse custom resource definition file' in e.value.message

def test_filter_max_operator_version_rcTrue():
    operator_versions = ['1.0.0-rc.1', '1.0.0-rc.2', '1.0.0-rc.3']
    insights_version = '1.0.0-rc.3'
    assert install.filter_max_operator_version(operator_versions, insights_version) == '1.0.0-rc.3'

def test_filter_max_operator_version_rcFalse():
    operator_versions = ['1.0.0-rc.1', '1.0.0-rc.2', '1.0.0']
    insights_version = '1.0.0'
    assert install.filter_max_operator_version(operator_versions, insights_version) == '1.0.0'

def test_filter_max_operator_version_no_match():
    operator_versions = ['1.0.0-rc.1', '1.0.0-rc.2', '1.0.1']
    insights_version = '2.0.0'
    assert install.filter_max_operator_version(operator_versions, insights_version) is None

def test_filter_max_operator_version_with_temp():
    operator_versions = ['1.6.0', '1.6.1-rc.1-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.1', '1.7.0']
    insights_version = '1.6.0'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res == '1.6.0'

def test_filter_max_operator_version_with_op_ahead():
    operator_versions = ['1.6.0', '1.6.1-rc.1-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.1', '1.6.1']
    insights_version = '1.6.0'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res == '1.6.1'

def test_filter_max_operator_version_with_temp_and_rc():
    operator_versions = ['1.6.0', '1.6.1-rc.2-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.1', '1.6.1']
    insights_version = '1.6.0-rc.2'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res == '1.6.1'

def test_filter_max_operator_version_with_no_matching_op():
    operator_versions = ['1.6.0', '1.6.1-rc.2-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.1', '1.6.1']
    insights_version = '1.7.0'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res is None

def test_filter_max_operator_version_with_no_matching_op_and_rc():
    operator_versions = ['1.6.0', '1.6.1-rc.2-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.1', '1.6.1']
    insights_version = '1.7.0-rc.1'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res is None

def test_filter_max_operator_version_with_op_released():
    operator_versions = ['1.6.0', '1.6.1-rc.1-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.3', '1.6.1']
    insights_version = '1.6.1-rc.1'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res == '1.6.1'

def test_filter_max_operator_version_with_rcs():
    operator_versions = ['1.6.0', '1.6.1-rc.3-mr-161holding+sha.ffa3b2b9', '1.6.1-rc.2', '1.6.0']
    insights_version = '1.6.1-rc.1'
    res = install.filter_max_operator_version(operator_versions, insights_version)
    assert res == '1.6.1-rc.2'


def test_check_for_operator_install_returns_version_to_install(mocker, k8s):
    # Operator not already installed, compatible version avaliable on repo
    mock_helm_env(mocker)
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s)
    assert install.check_for_operator_install('kx-insights', test_ns, chart, '1.3.0', None, force=True) == (True, False, '1.3.0', 'kx-insights', [])


def test_check_for_operator_install_errors_when_operator_repo_charts_not_compatible(mocker, k8s):
    # Operator not already installed, no compatible version avaliable on repo. Error returned
    mock_helm_env(mocker)
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, chart, '1.8.0', None, force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'Compatible version of operator not found' in e.value.message


def test_check_for_operator_install_does_not_install_when_no_repo_charts_available(mocker, k8s):
    # Operator already installed, no compatible version avaliable on repo
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocks.mock_assembly_list(k8s)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, chart, '1.8.0', None, force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'Compatible version of operator not found' in e.value.message

def test_check_for_operator_install_errors_when_installed_operator_not_compatible(mocker, k8s):
    # Incompatiable operator already installed, no version avaliable on repo. Error returned
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mocks.mock_assembly_list(k8s)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, chart, '1.8.0', None, force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'Compatible version of operator not found' in e.value.message


def test_check_for_operator_install_when_installed_and_available_operators_not_compatible(mocker, k8s):
    # Incompatible operator already installed, no compatible version available on repo. Error returned
    mock_helm_env(mocker)
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocks.mock_assembly_list(k8s)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, chart, '1.4.0', None, force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'Compatible version of operator not found' in e.value.message


def test_check_for_operator_install_when_provided_insights_and_operators_not_compatible(mocker, k8s):
    # Provided versions of operator and insights do not match minor versions
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocks.mock_assembly_list(k8s)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, 'insights', '1.3.0', '1.4.0', force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'kxi-operator version 1.4.0 is incompatible with insights version 1.3.0' in e.value.message

def mocked_get_installed_operator_versions_without_release(namespace):
        return (['1.2.0'], [None])

def test_check_for_operator_install_does_not_install_when_operator_is_not_managed_by_helm(mocker, k8s):
    # Operator already installed, no release-name annotation found.
    mock_helm_env(mocker)
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release)
    assert install.check_for_operator_install('kx-insights', test_ns, chart, '1.2.3', None, force=True) == (False, False, None, None, [])


def test_check_for_operator_install_errors_when_incompatible_operator_is_not_managed_by_helm(mocker, k8s):
    # Operator already installed with a version incompatible with insights, no release-name annotation found.
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocker.patch('kxicli.commands.install.get_installed_operator_versions', mocked_get_installed_operator_versions_without_release)
    with pytest.raises(Exception) as e:
        install.check_for_operator_install('kx-insights', test_ns, chart, '1.3.0', None, force=True)
    assert isinstance(e.value, click.ClickException)
    assert 'Compatible version of operator not found' in e.value.message


def test_check_for_operator_install_blocks_when_assemblies_running_in_other_namespaces(mocker, capfd, k8s):
    # Operator already installed, assemblies running in other namespaces
    mock_helm_env(mocker)
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    utils.mock_helm_repo_list(mocker)
    chart = helm_chart.Chart('kx-insights/insights')
    mock_kube_deployment_api(k8s, read=mocked_kube_deployment_list)
    mocks.mock_assembly_list(k8s,
                                             response=mock_list_assembly_multiple()
                                             )
    assert install.check_for_operator_install('kx-insights', test_ns, chart, '1.3.0', None, force=True) == (False, False, None, None, [])
    out, _ = capfd.readouterr()
    assert out == f"""kxi-operator already installed with version 1.2.3
warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
warn=Cannot upgrade kxi-operator
"""

def test_check_for_cluster_assemblies_returns_none(k8s):
    mocks.mock_assembly_list(k8s)
    assert not install.check_for_cluster_assemblies(exclude_namespace=test_ns)


def test_check_for_cluster_assemblies_prints_asm_list(k8s, capfd):
    mocks.mock_assembly_list(k8s, response=mock_list_assembly_multiple())
    assert install.check_for_cluster_assemblies(exclude_namespace=test_ns)
    out, _ = capfd.readouterr()
    assert out == f"""warn=Assemblies are running in other namespaces
ASSEMBLY NAME    NAMESPACE
basic-assembly   {utils.namespace()}
basic-assembly2  {utils.namespace()}
"""

def test_load_values_stores_with_file():
    assert install.load_values_stores(test_val_file) == test_vals

def test_load_values_stores_exception_when_values_file_does_not_exist():
    with pytest.raises(Exception) as e:
        install.load_values_stores('a-non-existent-file')
    assert isinstance(e.value, click.ClickException)
    assert 'File not found: a-non-existent-file. Exiting' in e.value.message


def test_load_values_stores_exception_when_invalid_values_file_provided():
    with temp_file(file_name='new_file') as new_file:
        with open(new_file, 'w') as f:
            f.write('test: {this is not a yaml')
        with pytest.raises(Exception) as e:
            install.load_values_stores(new_file)
        assert isinstance(e.value, click.ClickException)
        assert f'Invalid values file {new_file}' in e.value.message


def test_check_upgrade_version_allows_upgrade():
    assert install.check_upgrade_version('1.3.3', '1.4.0') == None
    assert install.check_upgrade_version('1.3.3', '1.3.4') == None
    assert install.check_upgrade_version('1.3.3', '2.0.0') == None
    assert install.check_upgrade_version('1.3.3', '1.3.3') == None
    assert install.check_upgrade_version('1.5.0-rc.18', '1.5.0-rc.19') == None
    assert install.check_upgrade_version('1.5.0-rc.18', '1.5.0-rc.18') == None

def test_check_upgrade_version_raises_exception_upon_downgrade():
    with pytest.raises(Exception) as e:
        install.check_upgrade_version('1.4.0', '1.3.3')
    assert isinstance(e.value, click.ClickException)
    assert 'Cannot upgrade from version 1.4.0 to version 1.3.3. Target version must be higher than currently installed version.' in e.value.message
    with pytest.raises(Exception) as e:
        install.check_upgrade_version('1.5.0-rc.18', '1.5.0-rc.17')
    assert isinstance(e.value, click.ClickException)
    assert 'Cannot upgrade from version 1.5.0-rc.18 to version 1.5.0-rc.17. Target version must be higher than currently installed version.' in e.value.message

def test_is_valid_upgrade_version_allows_upgrade(mocker):
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_installed_chart_json)
    assert install.is_valid_upgrade_version('test_release', test_ns, '1.4.0', phrases.check_installed) == True

def test_is_valid_upgrade_version_when_install_not_found(mocker):
    mocker.patch('kxicli.commands.install.get_installed_charts', lambda *args: [])
    assert install.is_valid_upgrade_version('test_release', test_ns, '1.4.0', phrases.check_installed) == False

def test_is_valid_upgrade_version_raises_exception_upon_downgrade(mocker):
    mocker.patch('kxicli.commands.install.get_installed_charts', mocked_installed_chart_json)
    with pytest.raises(Exception) as e:
        install.is_valid_upgrade_version('test_release', test_ns, '1.0.0', phrases.check_installed)
    assert isinstance(e.value, click.ClickException)
    assert 'Cannot upgrade from version 1.2.1 to version 1.0.0. Target version must be higher than currently installed version.' in e.value.message


def test_check_upgrade_version_allows_rollback():
    assert install.check_operator_rollback_version('1.3.3', '1.3.0') == None
    assert install.check_operator_rollback_version('1.3.3', '1.3.4') == None
    assert install.check_operator_rollback_version('1.3.3', '1.3.6') == None
    assert install.check_operator_rollback_version('1.3.3', '1.3.3') == None
    assert install.check_operator_rollback_version('1.5.0-rc.18', '1.5.0-rc.19') == None
    assert install.check_operator_rollback_version('1.5.0-rc.18', '1.5.0-rc.18') == None

def test_check_operator_rollback_version_raises_exception_upon_downgrade():
    with pytest.raises(Exception) as e:
        install.check_operator_rollback_version('1.4.0', '1.3.3')
    assert isinstance(e.value, click.ClickException)
    assert 'Insights rollback target version 1.4.0 is incompatible with target operator version 1.3.3. Minor versions must match.' in e.value.message
    with pytest.raises(Exception) as e:
        install.check_operator_rollback_version('1.5.0-rc.18', '1.4.0-rc.17')
    assert isinstance(e.value, click.ClickException)
    assert 'Insights rollback target version 1.5.0-rc.18 is incompatible with target operator version 1.4.0-rc.17. Minor versions must match.' in e.value.message

def test_get_values_and_secrets_from_helm_values_exist(mocker, k8s):
    mock_helm_repo_list(mocker, test_chart_repo_name, test_chart_repo_url)
    mocker.patch('kxicli.commands.install.helm.repo_update')
    test_val_data_updated = copy.deepcopy(test_vals)
    test_lic_secret = 'license-from-helm-values'
    test_val_data_updated['global']['license']['secretName'] = test_lic_secret
    mock_helm_get_values(mocker, test_val_data_updated)
    mock_validate_secret(mocker, True)
    mocker.patch('click.get_current_context')
    assert install.get_values_and_secrets(None,
                                          test_ns,
                                          'test_release',
                                          test_chart_repo_url,
                                          None,
                                          None
                                          ) == (None, test_ns, test_chart_repo_url, 'kxi-nexus-pull-secret', test_lic_secret)

def test_get_values_and_secrets_from_helm_values_dont_exist(mocker, capfd, k8s):
    mock_helm_repo_list(mocker, test_chart_repo_name, test_chart_repo_url)
    mocker.patch('kxicli.commands.install.helm.repo_update')
    test_val_data_updated = copy.deepcopy(test_vals)
    test_lic_secret = 'license-from-helm-values'
    test_val_data_updated['global']['license']['secretName'] = test_lic_secret
    mock_helm_get_values(mocker, test_val_data_updated)
    mock_validate_secret(mocker, False)
    mocker.patch('click.get_current_context')
    with pytest.raises(click.ClickException) as e:
        install.get_values_and_secrets(None,
                                          test_ns,
                                          'test_release',
                                          test_chart_repo_url,
                                          None,
                                          None
                                          )
    assert isinstance(e.value, click.ClickException)
    assert e.value.message == 'Validation failed, run "kxi install setup" to fix'
    out, _ = capfd.readouterr()
    assert out == f"""Validating values...
error=Required secret {test_lic_secret} does not exist
error=Required secret kxi-certificate does not exist
error=Required secret kxi-nexus-pull-secret does not exist
error=Required secret kxi-keycloak does not exist
error=Required secret kxi-postgresql does not exist
"""


def test_get_values_and_secrets_from_helm_values_exist_called_from_azure(mocker, k8s):
    test_val_data_updated = copy.deepcopy(test_vals)
    test_val_data_updated['global']['image']['repository'] = 'test-repo.com'
    mock_helm_get_values(mocker, test_val_data_updated)
    mock_validate_secret(mocker, True)
    ctx_mock = mocker.patch('click.get_current_context')
    ctx_mock.return_value.parent.info_name = 'azure'
    assert install.get_values_and_secrets(None,
                                          test_ns,
                                          'test_release',
                                          test_chart_repo_url,
                                          None,
                                          None
                                          ) == (None, test_ns, test_chart_repo_url, 'kxi-nexus-pull-secret', 'kxi-license')


def test_get_operator_location_when_remote(mocker):
    mocker.patch("kxicli.resources.helm.repo_exists")
    mocker.patch("kxicli.resources.helm.repo_update")
    chart = helm_chart.Chart('kx-insights/insights')
    assert install.get_operator_location(chart, '1.2.3') == 'kx-insights/kxi-operator'


def test_get_operator_location_when_local(mocker):
    chart = helm_chart.Chart(str(insights_tgz))
    assert install.get_operator_location(chart, '1.2.3') == str(Path(__file__).parent / 'files/helm/kxi-operator-1.2.3.tgz')


def test_local_chart_versions_happy_path():
    chart = helm_chart.Chart(str(insights_tgz))
    assert install.local_chart_versions(chart) == ['1.2.3']


def test_local_chart_versions_returns_none_correctly():
    chart = helm_chart.Chart(str(insights_tgz))
    assert install.local_chart_versions(chart, prefix = 'unknown-chart-') == []


def test_get_chart_actions_with_no_upgrade_actions(mocker):
    chart = helm_chart.Chart(str(insights_tgz))
    mock_helm_env(mocker)

    def mocked_extract(*args):
        raise Exception("Path does not exist")

    mocker.patch('kxicli.common.extract_files_from_tar', mocked_extract)
    assert install.get_chart_actions(chart, "1.2.3") is None


def test_get_chart_actions_with_upgrade_actions(mocker):
    chart = helm_chart.Chart(str(insights_tgz))
    mock_helm_env(mocker)

    # mock data returned from tar extraction
    mocked_extract = mocker.patch('kxicli.common.extract_files_from_tar')
    mocked_extract.return_value = ["""
    changes:
      - version:
          - 1.6.1
          - 1.7.0
        name: 'This is an example upgrade'
        upgrade:
          - delete: ['-n', '$NAMESPACE', 'sts/$RELEASE-qe-resource-coordinator']
        rollback: true
    """]

    actions = {"changes": [{
        "version": ["1.6.1", "1.7.0"],
        "name": "This is an example upgrade",
        "upgrade": [{"delete": ["-n", "$NAMESPACE", "sts/$RELEASE-qe-resource-coordinator"]}],
        "rollback": True
    }]}

    assert actions == install.get_chart_actions(chart, "1.2.3")


def test_running_upgrade_without_chart_actions(mocker):
    mocked_get_charts = mocker.patch("kxicli.commands.install.get_installed_charts")
    mocked_actions = mocker.patch("kxicli.commands.install.get_chart_actions")

    mocked_get_charts.return_value = [{"app_version": "1.2.0"}]
    mocked_actions.return_value = None
    chart = helm_chart.Chart(str(insights_tgz))
    assert install.run_chart_actions(chart, 'insights', 'kxi', '1.2.3') is None


def test_running_upgrade_with_delete_action(mocker):
    namespace = "kxi"
    release = "insights"

    def mocked_subprocess(args, **kwargs):
        assert args == ["kubectl", "delete", "-n", namespace,
                        "service/" + release + "-resource-coordinator"]

    mocked_actions = mocker.patch("kxicli.commands.install.get_chart_actions")
    mocked_get_charts = mocker.patch("kxicli.commands.install.get_installed_charts")
    mocker.patch("subprocess.run", mocked_subprocess)

    mocked_actions.return_value = {"changes": [{
        "version": ["1.2.1"],
        "name": "Update Resource Coordinator service to be headless",
        "upgrade": [{"delete": ["-n", "$NAMESPACE", "service/$RELEASE-resource-coordinator"]}],
        "rollback": True
    }]}
    mocked_get_charts.return_value = [{"app_version": "1.2.0"}]

    chart = helm_chart.Chart(str(insights_tgz))
    assert install.run_chart_actions(chart, release, namespace, '1.2.3') is None


def test_running_upgrade_with_multiple_versions(mocker):
    namespace = "kxi"
    release = "insights"
    commands = [
        ["kubectl", "delete", "-n", namespace,
         "service/" + release + "-resource-coordinator"],
        ["kubectl", "delete", "-n", namespace,
         "deployment/" + release + "-qe-gateway"]
    ]
    subprocess_call = 0

    def mocked_subprocess(args, **kwargs):
        nonlocal subprocess_call
        assert args == commands[subprocess_call]
        subprocess_call += 1

    mocked_actions = mocker.patch("kxicli.commands.install.get_chart_actions")
    mocked_get_charts = mocker.patch("kxicli.commands.install.get_installed_charts")
    mocker.patch("subprocess.run", mocked_subprocess)

    mocked_actions.return_value = {"changes": [{
        "version": ["1.2.1"],
        "name": "Update Resource Coordinator service to be headless",
        "upgrade": [{"delete": ["-n", "$NAMESPACE", "service/$RELEASE-resource-coordinator"]}],
        "rollback": True
    }, {
        "version": ["1.2.2"],
        "name": "Upgrade labels on QE gateways",
        "upgrade": [{"delete": ["-n", "$NAMESPACE", "deployment/$RELEASE-qe-gateway"]}],
        "rollback": True
    }, {
        "version": ["1.2.4"],
        "name": "Unused upgrade",
        "upgrade": [{"delete": ["-n", "$NAMESPACE", "should-not-run"]}]
    }]}
    mocked_get_charts.return_value = [{"app_version": "1.2.0"}]
    chart = helm_chart.Chart(str(insights_tgz))
    assert install.run_chart_actions(chart, release, namespace, '1.2.3') is None


def test_apply_envs():
    args = ['-n', '$NAMESPACE', 'sts/$RELEASE-resource-coordinator']
    env = {'NAMESPACE': 'kxi', 'RELEASE': 'insights'}
    exp = ['-n', 'kxi', 'sts/insights-resource-coordinator']
    assert exp == install.apply_envs(args, env)


def test_version_within():
    assert install.version_within('1.0.0', '0.0.0', '2.0.0')
    assert install.version_within('1.2.1', '1.2.0', '1.2.1')
    assert install.version_within('1.2.1', '0.9.9', '1.3.2')
    assert install.version_within('1.2.1-rc.10', '1.2.1-rc.2', '1.2.2')
    assert not install.version_within('1.3.0', '2.1.1', '2.1.2')
    assert not install.version_within('1.1.1', '1.2.0', '1.0.0')
