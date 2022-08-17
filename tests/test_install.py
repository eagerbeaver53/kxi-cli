"""This install test is meant to unit test the individual functions in the install command"""
import base64
import copy
import io
import json
import kubernetes as k8s
import pytest
import yaml
import click

from kxicli import common
from kxicli.commands import install
from kxicli.resources import secret
from utils import IPATH_KUBE_COREV1API, test_secret_data, test_secret_type, test_secret_key, \
    mock_kube_secret_api, mocked_read_namespaced_secret, raise_not_found, test_val_file, mock_validate_secret, mock_helm_env
from test_install_e2e import mocked_read_namespaced_secret_return_values, test_vals
from const import test_user, test_pass, test_lic_file

# Common test parameters
test_ns = 'test-ns'
test_repo = 'test.kx.com'
test_secret = 'test-secret'
test_key = install.gen_private_key()
test_cert = install.gen_cert(test_key)

common.config.load_config("default")

# Constants for common import paths
IPATH_CLICK_PROMPT = 'click.prompt'
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
        stdout='[{"name":"kx-insights/kxi-operator","version":"1.1.0","app_version":"1.1.0-rc.43","description":"KX Insights Operator"}]\n'
    )


def mocked_helm_search_returns_empty_json(base_command, check=True, capture_output=True, text=True):
    return install.subprocess.CompletedProcess(
        args=base_command,
        returncode=0,
        stdout='[]\n'
    )


def test_get_secret_body_string_data_parameter():
    sdata = {'a': 'b'}

    expected = k8s.client.V1Secret()
    expected.metadata = k8s.client.V1ObjectMeta(namespace=test_ns, name=test_secret)
    expected.type = test_secret_type
    expected.string_data = sdata

    s = secret.Secret(test_ns, test_secret, test_secret_type, string_data=sdata)

    assert s.get_body() == expected


def test_get_secret_body_data_parameter():
    data = {'a': 'b'}

    expected = k8s.client.V1Secret()
    expected.metadata = k8s.client.V1ObjectMeta(namespace=test_ns, name=test_secret)
    expected.type = test_secret_type
    expected.data = data
    s = secret.Secret(test_ns, test_secret, test_secret_type, data=data)

    assert s.get_body() == expected


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


def test_create_docker_secret(mocker):
    mock_kube_secret_api(mocker)

    test_cfg = install.create_docker_config(test_repo, test_user, test_pass)

    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_DOCKERCONFIG_JSON, install.IMAGE_PULL_KEYS)
    res = install.populate_docker_config_secret(s, test_cfg).get_body()

    assert res.type == 'kubernetes.io/dockerconfigjson'
    assert res.metadata.name == test_secret
    assert '.dockerconfigjson' in res.data


def test_create_license_secret_encoded(mocker):
    mock_kube_secret_api(mocker)

    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.LICENSE_KEYS)
    s, _ = install.populate_license_secret(s, test_lic_file, True)
    res = s.get_body()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'license' in res.string_data
    with open(test_lic_file, 'rb') as license_file:
        assert base64.b64decode(res.string_data['license']) == license_file.read()


def test_create_license_secret_decoded(mocker):
    mock_kube_secret_api(mocker)

    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.LICENSE_KEYS)
    s, _ = install.populate_license_secret(s, test_lic_file, False)
    res = s.get_body()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'license' in res.data
    with open(test_lic_file, 'rb') as license_file:
        assert base64.b64decode(res.data['license']) == license_file.read()


def test_create_tls_secret(mocker):
    mock_kube_secret_api(mocker)

    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_TLS)
    s = install.populate_tls_secret(s, test_cert, test_key)
    res = s.get_body()

    assert res.type == 'kubernetes.io/tls'
    assert res.metadata.name == test_secret
    assert 'tls.crt' in res.data
    assert 'tls.key' in res.data


def test_read_secret_returns_k8s_secret(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)

    s = secret.Secret(test_ns, test_secret)
    res = s.read()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert res.data == test_secret_data


def test_read_secret_returns_empty_when_does_not_exist(mocker):
    mock = mocker.patch(IPATH_KUBE_COREV1API)
    mock.return_value.read_namespaced_secret.side_effect = raise_not_found
    s = secret.Secret(test_ns, test_secret)
    res = s.read()

    assert res == None


def test_get_install_config_secret_returns_decoded_secret(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret_return_values)

    s = secret.Secret(test_ns, test_secret)
    res = install.get_install_config_secret(s)

    assert res == yaml.dump(test_vals)


def test_get_install_config_secret_when_does_not_exist(mocker):
    mock_kube_secret_api(mocker)
    s = secret.Secret(test_ns, test_secret)
    res = install.get_install_config_secret(s)

    assert res == None


def test_patch_secret_returns_updated_k8s_secret(mocker):
    mock_kube_secret_api(mocker)
    s = secret.Secret(test_ns, test_secret, test_secret_type)
    s.data = {"secret_key": "new_value"}
    res = s.patch()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert res.data == s.data


def test_create_install_config_secret_when_does_not_exists(mocker):
    mock_kube_secret_api(mocker)

    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.INSTALL_CONFIG_KEYS)
    s = install.create_install_config(s, test_vals)
    res = s.get_body()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'values.yaml' in res.data
    assert yaml.full_load(base64.b64decode(res.data['values.yaml'])) == test_vals


def test_create_install_config_secret_when_secret_exists_and_user_overwrites(mocker,monkeypatch):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret_return_values)

    # Create new values to write to secret
    new_values = {"secretName": "a_test_secret_name"}

    # patch stdin to 'y' for the prompt confirming to overwrite the secret
    monkeypatch.setattr(SYS_STDIN, io.StringIO('y'))
    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.INSTALL_CONFIG_KEYS)
    install.create_install_config(s, new_values)
    res = s.get_body()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'values.yaml' in res.data
    # assert that secret is updated with new_values
    assert yaml.full_load(base64.b64decode(res.data['values.yaml'])) == new_values


def test_create_install_config_secret_when_secret_exists_and_user_declines_overwrite(mocker, monkeypatch):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret_return_values)

    # update contents of values to write to secret
    new_values = {"secretName": "a_test_secret_name"}

    # patch stdin to 'n' for the prompt, declining to overwrite the secret
    monkeypatch.setattr(SYS_STDIN, io.StringIO('n'))
    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.INSTALL_CONFIG_KEYS)
    s = install.create_install_config(s, new_values)
    res = s.read()

    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert 'values.yaml' in res.data
    # assert that secret is unchanged
    assert yaml.full_load(base64.b64decode(res.data['values.yaml'])) == test_vals


def test_build_install_secret():
    data = {"secretName": "a_test_secret_name"}
    s = secret.Secret(test_ns, test_secret, install.SECRET_TYPE_OPAQUE, install.INSTALL_CONFIG_KEYS)
    s = install.populate_install_secret(s, {'values': data})
    res = s.get_body().data

    assert 'values.yaml' in res
    assert yaml.full_load(base64.b64decode(res['values.yaml'])) == data


def test_get_install_values_returns_values_from_secret(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret_return_values)
    print(install.get_install_values(secret.Secret(test_ns, test_secret)))

    assert install.get_install_values(secret.Secret(test_ns, test_secret)) == yaml.dump(test_vals)
    assert install.get_install_values(secret.Secret(test_ns, None)) is None


def test_get_install_values_exits_when_secret_not_found(mocker):
    mock = mocker.patch(IPATH_KUBE_COREV1API)
    mock.return_value.read_namespaced_secret.side_effect = raise_not_found
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        install.get_install_values(secret.Secret(test_ns, test_secret))
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == 1


def test_get_operator_version_returns_operator_version_if_passed_regardless_of_rc():
    non_rc = install.get_operator_version('kxi-insights', '1.2.3', '4.5.6')
    rc = install.get_operator_version('kxi-insights', '1.2.3-rc.1', '4.5.6')

    assert non_rc == '4.5.6'
    assert rc == '4.5.6'


def test_get_operator_version_returns_latest_minor_version(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_valid_json)
    assert install.get_operator_version('kxi-insights', '1.1.1', None) == '1.1.0'


def test_get_operator_version_returns_error_when_not_found(mocker):
    mocker.patch('subprocess.run', mocked_helm_search_returns_empty_json)
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        install.get_operator_version('kxi-insights', '5.6.7', None)
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == 1


def test_get_installed_charts_returns_chart_json(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_valid_json)
    assert install.get_installed_charts('insights', test_ns) == json.loads(mocked_helm_list_returns_valid_json(''))


def test_insights_installed_returns_true_when_already_exists(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_valid_json)
    assert install.insights_installed('insights', test_ns) == True


def test_insights_installed_returns_false_when_does_not_exist(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_empty_json)
    assert install.insights_installed('insights', test_ns) == False


def test_operator_installed_returns_true_when_already_exists(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_valid_json)
    assert install.operator_installed('insights') == True


def test_operator_installed_returns_false_when_does_not_exist(mocker):
    mocker.patch(fun_subprocess_check_output, mocked_helm_list_returns_empty_json)
    assert install.operator_installed('insights') == False


def test_sanitize_auth_url():
    https_replaced = install.sanitize_auth_url('https://keycloak.keycloak.svc.cluster.local/auth/')
    trailing_slash = install.sanitize_auth_url('https://keycloak.keycloak.svc.cluster.local/auth')
    prepend_http = install.sanitize_auth_url('keycloak.keycloak.svc.cluster.local/auth')

    expected = 'http://keycloak.keycloak.svc.cluster.local/auth/'
    assert https_replaced == expected
    assert trailing_slash == expected
    assert prepend_http == expected


def test_get_image_and_license_secret_from_values_returns_defaults():
    assert install.get_image_and_license_secret_from_values(None, None, None, None) == (
    'kxi-nexus-pull-secret', 'kxi-license')


def test_get_image_and_license_secret_from_values_returns_from_secret():
    test_vals_secret = copy.deepcopy(test_vals)
    test_vals_secret['global']['imagePullSecrets'] = [{'name': 'image-pull-from-secret'}]
    test_vals_secret['global']['license']['secretName'] = 'license-from-secret'
    assert install.get_image_and_license_secret_from_values(str(test_vals_secret), None, None, None) == (
    'image-pull-from-secret', 'license-from-secret')


def test_get_image_and_license_secret_from_values_file_overrides_secret():
    test_vals_secret = copy.deepcopy(test_vals)
    test_vals_secret['global']['imagePullSecrets'] = [{'name': 'image-pull-from-secret'}]
    test_vals_secret['global']['license']['secretName'] = 'license-from-secret'
    assert install.get_image_and_license_secret_from_values(str(test_vals_secret), test_val_file, None, None) == (
    'kxi-nexus-pull-secret', 'kxi-license')


def test_get_image_and_license_secret_from_values_args_overrides_secret_and_file():
    test_vals_secret = copy.deepcopy(test_vals)
    test_vals_secret['global']['imagePullSecrets'] = [{'name': 'image-pull-from-secret'}]
    test_vals_secret['global']['license']['secretName'] = 'license-from-secret'
    assert install.get_image_and_license_secret_from_values(str(test_vals_secret), test_val_file, 'image-pull-from-arg',
                                                            'license-from-arg') == (
           'image-pull-from-arg', 'license-from-arg')


def test_get_image_and_license_secret_returns_error_when_invalid_secret_passed():
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        install.get_image_and_license_secret_from_values(test_lic_file, None, None, None)
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == 1


def test_get_image_and_license_secret_returns_error_when_invalid_file_passed():
    with pytest.raises(SystemExit) as pytest_wrapped_e:
        install.get_image_and_license_secret_from_values(None, test_lic_file, None, None)
    assert pytest_wrapped_e.type == SystemExit
    assert pytest_wrapped_e.value.code == 1


def test_get_missing_key_with_no_dict_returns_all_keys():
    keys = ('a','b')
    assert list(keys) == secret.Secret(test_ns, test_secret, required_keys = keys).get_missing_keys(None)


def test_get_missing_key_with_key_missing():
    assert ['a'] == secret.Secret(test_ns, test_secret, required_keys = ('a','b')).get_missing_keys({'b': 2, 'c': 3})


def test_get_missing_key_with_no_key_missing():
    assert [] == secret.Secret(test_ns, test_secret, required_keys = ('a', 'b')).get_missing_keys({'a': 1, 'b': 2})


def test_validate_secret_when_no_secret_exists(mocker):
    mock_kube_secret_api(mocker)
    assert (False, True, []) == secret.Secret(test_ns, test_secret, test_secret_type, ['test']).validate()


def test_validate_secret_when_missing_a_key(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    assert (True, False, ['test']) == secret.Secret(test_ns, test_secret, test_secret_type, ['test']).validate()


def test_validate_secret_when_incorrect_type(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    assert (True, False, []) == secret.Secret(test_ns, test_secret, install.SECRET_TYPE_TLS, (test_secret_key,)).validate()


def test_ensure_secret_when_does_not_exist(mocker):
    mock_kube_secret_api(mocker)

    key = 'a'
    secret_data = {key: 1}
    s = secret.Secret(test_ns, test_secret, test_secret_type)
    res = install.ensure_secret(s, populate, data=secret_data)

    assert res.type == test_secret_type
    assert res.name == test_secret
    assert key in res.data
    assert res.data[key] == 1


def test_ensure_secret_when_secret_exists_and_is_valid(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    s = secret.Secret(test_ns, test_secret)
    res = install.ensure_secret(s, populate, data=test_secret_data)

    assert res.type is None
    assert res.name == test_secret
    # populate function should not be called to update the data because it's already valid
    assert res.data is None

def test_ensure_secret_when_secret_exists_but_is_invalid_w_overwrite(mocker, monkeypatch):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    mock_validate_secret(mocker, is_valid=False)
    
    # patch stdin to 'n' for the prompt rejecting secret overwrite
    monkeypatch.setattr(SYS_STDIN, io.StringIO('y'))

    new_key = 'xyz'
    new_data = {new_key: 123}

    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = install.ensure_secret(s, populate, data=new_data)

    assert res.type == test_secret_type
    assert res.name == test_secret
    assert new_key in res.data
    assert res.data[new_key] == new_data[new_key]


def test_ensure_secret_when_secret_exists_but_is_invalid_w_no_overwrite(mocker, monkeypatch):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    mock_validate_secret(mocker, is_valid=False)

    # patch stdin to 'n' for the prompt rejecting secret overwrite
    monkeypatch.setattr(SYS_STDIN, io.StringIO('n'))

    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = install.ensure_secret(s, populate, data={'a': 1})

    assert res.type == test_secret_type
    assert res.name == test_secret
    assert test_secret_key in res.data
    assert res.data[test_secret_key] == test_secret_data[test_secret_key]


def test_create_secret_returns_k8s_secret(mocker):
    mock_kube_secret_api(mocker)

    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = s.create()

    assert res.metadata.namespace == test_ns
    assert res.type == test_secret_type
    assert res.metadata.name == test_secret
    assert res.data == test_secret_data


def test_create_secret_returns_exception(mocker):
    mock_kube_secret_api(mocker, create=raise_not_found)
    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = s.create()

    assert isinstance(res, k8s.client.exceptions.ApiException)
    assert res.status == 404


def test_patch_secret_returns_exception(mocker):
    mock_kube_secret_api(mocker, patch=raise_not_found)
    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    res = s.patch()

    assert isinstance(res, k8s.client.exceptions.ApiException)
    assert res.status == 404


def test_exists_returns_true_when_exists(mocker):
    mock_kube_secret_api(mocker, read=mocked_read_namespaced_secret)
    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    assert s.exists()


def test_exists_returns_false_when_does_not_exist(mocker):
    mock_kube_secret_api(mocker)
    s = secret.Secret(test_ns, test_secret, test_secret_type, data=test_secret_data)
    assert s.exists() == False


def test_read_cache_crd_from_file_throws_yaml_error(mocker):
    mock_helm_env(mocker)

    # mock data returned from tar extraction
    mocker.patch('kxicli.common.extract_files_from_tar', return_value=['abc: 123\n    def: 456'])
    with pytest.raises(Exception) as e:
        install.read_cached_crd_files(
            '1.2.3',
            'kxi-operator',
            [install.CRD_FILES[0]]
            )

    assert isinstance(e.value, click.ClickException)
    assert 'Failed to parse custom resource definition file' in e.value.message
