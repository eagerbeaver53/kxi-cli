from functools import partial
import json
import os
import time
from unittest.mock import MagicMock
import pytest
import uuid

from click import ClickException
from click.testing import CliRunner
from utils import return_none
from kxi.auth import Authorizer
from kxicli import main, common
from kxicli.resources import auth
from kxi.entitlement import Entitlement, Actor
from kxicli.commands.entitlement import _parse_groups

import mocks

TEST_CLI = CliRunner()

test_cli_config = os.path.dirname(__file__) + "/files/test-cli-config"
common.config.config_file = test_cli_config
common.config.load_config("default")


@pytest.fixture
def sample_entity():
    return Entitlement(
        id="1d22c78c-884b-4b24-88f8-dbcf23ef7bfe",
        entity="sample",
        entityType="assembly",
        owner="00000000-0000-0000-0000-000000000000",
        groups=[
            "b621b5a0-a346-4bce-910a-18a9661319a6",
            "00dc3afa-abae-4a6b-be52-8fd090897c97"
        ]
    )

@pytest.fixture
def sample_entity_with_users(sample_entity):
    sample_entity.users = [
        "99283923-8c1b-41da-ab70-59e917472181",
        "8c02029c-84e6-481c-acd9-8efb39e4ce56",
        "5b12db9a-858f-4e02-adc7-3c266e6e4c01"
    ]
    return sample_entity

@pytest.fixture
def sample_actor():
    return Actor(
        id="3a536641-0492-4c44-997b-e3259847239b",
        name="quants",
        path="/quants"
    )


@pytest.fixture
def rest_api_mock(mocker):
    session = mocker.patch("kxi.auth.Authorizer.session")
    return session


def test__parse_groups_raises_invalid_uuid():
    with pytest.raises(ClickException, match="123 is not a valid UUID"):
        _parse_groups("123")

current_time = int(time.time())
expires_at = current_time + 3600 
TEST_SERVICE_ACCOUNT_TOKEN = {
    "access_token": "abc1234",
    "token_type": "Bearer",
    "expires_in": 3600,
    "refresh_token": "abc1234",
    "created_at": 1652741000,
    "expires_at": expires_at
}

@pytest.fixture
def mock_auth_functions(mocker):
    mocker.patch.object(Authorizer, 'fetch_token', return_value=MagicMock(access_token=TEST_SERVICE_ACCOUNT_TOKEN))
    mocker.patch.object(Authorizer, 'token', return_value=TEST_SERVICE_ACCOUNT_TOKEN)
    mocker.patch.object(Authorizer, '_check_cached_token', return_value=TEST_SERVICE_ACCOUNT_TOKEN)
    
    mocker.patch('kxicli.resources.auth.check_cached_token_active', return_none)
    mocker.patch('kxicli.resources.auth.get_serviceaccount_token', return_none)
    os.environ["INSIGHTS_CLIENT_ID"] = "test-client"
    os.environ["INSIGHTS_CLIENT_SECRET"] = ""
    
def test_entitlement_list(rest_api_mock, sample_entity, mock_auth_functions):
    r = f"[{sample_entity.json()}]".encode("utf-8")
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "list"])

    assert result.exit_code == 0
    assert result.output == f"[{sample_entity.json()}]\n"
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0] == ("https://test.kx.com/entitlements/v1/entities",)


def test_entitlement_list_with_users(rest_api_mock, sample_entity_with_users, mock_auth_functions):
    r = f"[{sample_entity_with_users.json()}]".encode("utf-8")
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "list"])

    assert result.exit_code == 0
    assert result.output == f"[{sample_entity_with_users.json()}]\n"
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0] == ("https://test.kx.com/entitlements/v1/entities",)


def test_entitlement_get(rest_api_mock, sample_entity, mock_auth_functions):
    r = sample_entity.json().encode("utf-8")
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "get", str(sample_entity.id)])

    assert result.output == f"{sample_entity.json()}\n"
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0] == (f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}",)


def test_entitlement_delete_with_force(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.delete.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "delete", str(sample_entity.id), "--force"])

    assert result.exit_code == 0
    rest_api_mock.delete.assert_called_once()
    assert rest_api_mock.delete.call_args[0] == (f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}",)


def test_entitlement_delete_with_positive_confirmation(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.delete.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "delete", str(sample_entity.id)], input="y")

    assert result.exit_code == 0
    assert "Are you sure you want to delete this entitlement?" in result.output
    rest_api_mock.delete.assert_called_once()
    assert rest_api_mock.delete.call_args[0] == (f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}",)


def test_entitlement_delete_with_negative_confirmation(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.delete.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "delete", str(sample_entity.id)], input="n")

    assert result.exit_code == 0
    assert "Are you sure you want to delete this entitlement?" in result.output
    assert not rest_api_mock.delete.called

def test_entitlement_delete_with_force_no_prompt(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.delete.return_value = mocks.http_response("", status_code=200)


    result = TEST_CLI.invoke(main.cli, ["entitlement", "delete", str(sample_entity.id), "--force"])

    assert result.exit_code == 0
    # delete should be called and there should be no Y/N prompt for deletion
    assert result.output == '{}\n'
    assert rest_api_mock.delete.called


def test_entitlement_actors(rest_api_mock, sample_actor, mock_auth_functions):
    r = f"[{sample_actor.json()}]".encode("utf-8")
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)

    result = TEST_CLI.invoke(main.cli, ["entitlement", "actors"])

    assert result.exit_code == 0
    assert result.output == f"[{sample_actor.json()}]\n"
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0] == ("https://test.kx.com/entitlements/v1/actors",)


def test_entitlement_create(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.post.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "create",
                                str(sample_entity.id),
                                sample_entity.entity,
                                sample_entity.entityType.value
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.post.assert_called_once()
    expected = Entitlement(id=sample_entity.id, entity=sample_entity.entity,
                           entityType=sample_entity.entityType,
                           groups=[]).json(exclude_defaults=True)
    assert rest_api_mock.post.call_args[0] == (
        "https://test.kx.com/entitlements/v1/entities",
        expected,
        None
    )


def test_entitlement_create_with_owner(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.post.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "create",
                                str(sample_entity.id),
                                sample_entity.entity,
                                sample_entity.entityType.value,
                                "--owner", str(sample_entity.owner)
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.post.assert_called_once()
    expected = Entitlement(id=sample_entity.id, entity=sample_entity.entity,
                           entityType=sample_entity.entityType,
                           owner=sample_entity.owner,
                           groups=[]).json(exclude_defaults=True)
    assert rest_api_mock.post.call_args[0] == (
        "https://test.kx.com/entitlements/v1/entities",
        expected,
        None
    )


def test_entitlement_create_with_owner_and_groups(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.post.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "create",
                                str(sample_entity.id),
                                sample_entity.entity,
                                sample_entity.entityType.value,
                                "--owner", str(sample_entity.owner),
                                "--groups", f"{str(sample_entity.groups[0])},{str(sample_entity.groups[1])}"
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.post.assert_called_once()
    expected = Entitlement(id=sample_entity.id, entity=sample_entity.entity,
                           entityType=sample_entity.entityType,
                           owner=sample_entity.owner,
                           groups=sample_entity.groups).json(exclude_defaults=True)
    assert rest_api_mock.post.call_args[0] == (
        "https://test.kx.com/entitlements/v1/entities",
        expected,
        None
    )


def test_entitlement_update_with_name(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.patch.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "update",
                                str(sample_entity.id),
                                "--name", "new name"
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.patch.assert_called_once()
    expected = Entitlement(id=sample_entity.id, entity="new name", groups=[]
                           ).json(
        exclude_defaults=True, exclude={"id"})
    assert rest_api_mock.patch.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"
    assert rest_api_mock.patch.call_args[0][1] == expected


def test_entitlement_update_with_owner(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.patch.return_value = mocks.http_response("", status_code=200)
    owner = uuid.UUID(int=123)
    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "update",
                                str(sample_entity.id),
                                "--owner", str(owner)
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.patch.assert_called_once()
    expected = Entitlement(id=sample_entity.id, owner=owner, groups=[]
                           ).json(
        exclude_defaults=True, exclude={"id"})
    assert rest_api_mock.patch.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"
    assert rest_api_mock.patch.call_args[0][1] == expected


def test_entitlement_update_with_groups(rest_api_mock, sample_entity, mock_auth_functions):
    rest_api_mock.patch.return_value = mocks.http_response("", status_code=200)
    g1 = uuid.UUID(int=123)
    g2 = sample_entity.groups[0]

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "update",
                                str(sample_entity.id),
                                "--groups", f"{str(g1)},{str(g2)}"
                             ]
                             )

    assert result.exit_code == 0
    rest_api_mock.patch.assert_called_once()
    expected = Entitlement(id=sample_entity.id, groups=[g1, g2]
                           ).json(
        exclude_defaults=True, exclude={"id"})
    assert rest_api_mock.patch.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"
    assert rest_api_mock.patch.call_args[0][1] == expected


def test_entitlement_add_groups_with_dup(rest_api_mock, sample_entity, mock_auth_functions):
    g1 = uuid.UUID(int=123)
    g2 = sample_entity.groups[0]
    g3 = sample_entity.groups[1]
    r = sample_entity.json().encode("utf-8")
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)
    rest_api_mock.patch.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "add-groups",
                                str(sample_entity.id),
                                f"{str(g1)},{str(g2)}"
                             ]
                             )

    assert result.exit_code == 0

    # validate the entity was fetched
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"

    # validate the patch request had the correct groups
    rest_api_mock.patch.assert_called_once()
    expected = Entitlement(id=sample_entity.id, groups=[g2, g3, g1]
                           ).json(
        exclude_defaults=True, exclude={"id"})
    assert rest_api_mock.patch.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"
    assert rest_api_mock.patch.call_args[0][1] == expected


def test_entitlement_rm_groups_with_extra(rest_api_mock, sample_entity, mock_auth_functions):
    r = sample_entity.json().encode("utf-8")
    g1 = sample_entity.groups[0]
    g2 = uuid.UUID(int=123)
    rest_api_mock.get.return_value = mocks.http_response("", status_code=200, content=r)
    rest_api_mock.patch.return_value = mocks.http_response("", status_code=200)

    result = TEST_CLI.invoke(main.cli,
                             [
                                "entitlement",
                                "rm-groups",
                                str(sample_entity.id),
                                f"{str(g1)},{str(g2)}"
                             ]
                             )

    assert result.exit_code == 0

    # validate the entity was fetched
    rest_api_mock.get.assert_called_once()
    assert rest_api_mock.get.call_args[0] == (f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}",)

    # validate the patch request had the correct groups
    rest_api_mock.patch.assert_called_once()
    expected = Entitlement(id=sample_entity.id, groups=[sample_entity.groups[1]]
                           ).json(
        exclude_defaults=True, exclude={"id"})
    assert rest_api_mock.patch.call_args[0][0] == f"https://test.kx.com/entitlements/v1/entities/{sample_entity.id}"
    assert rest_api_mock.patch.call_args[0][1] == expected
