from const import test_namespace, test_cluster, test_host, test_chart_repo_name, test_chart_repo_url, \
    test_image_repo, test_user, test_pass, test_cert, test_key, test_docker_config_json, test_lic_file
from kxicli import common
from kxicli import phrases

def append_message(message, new_message):
    if not new_message:
        return message
    if len(message):
        message = message + '\n'
    return message + new_message

def cli_input(
    verb,
    output_file = None,
    incluster = False,
    provide_ingress_cert = 'n',
    ingress_cert = None,
    ingress_cert_source = None,
    ingress_key = None,
    ingress_key_source = None,
    lic = test_lic_file,
    lic_sec_exists = False,
    lic_sec_is_valid = True,
    lic_overwrite = 'y',
    repo = test_image_repo,
    user = test_user,
    image_sec_exists = False,
    image_sec_is_valid = True,
    image_sec_overwrite = 'y',
    use_existing_creds = 'n',
    client_sec_exists = False,
    client_sec_is_valid = True,
    client_overwrite = 'y',
    kc_secret_exists = False,
    kc_secret_is_valid = True,
    kc_secret_overwrite = 'y',
    pg_secret_exists = False,
    pg_secret_is_valid = True,
    pg_secret_overwrite = 'y',
    gui_secret_source = 'generated',
    operator_secret_source = 'generated',
    deploy_keycloak=True,
    values_exist = False,
    overwrite_values = 'y',
    install_config_exists = False,
    overwrite_install_config = 'n',
    hostname = test_host,
    hostname_source='config',
    chart_repo_existing=None,
    chart_repo_name=test_chart_repo_name,
    chart_repo_name_source='prompt',
    chart_repo_url=test_chart_repo_url,
    chart_repo_url_source='prompt',
    chart_user=test_user,
    chart_user_source='prompt',
    chart_pass=test_pass

):
    # TODO: Implement full run support
    if verb in  ('run', 'upgrade'):
        return ''

    inp = ''
    # Hostname
    if hostname_source == 'prompt':
        inp = append_message(inp, hostname)

    # Ingress
    if ingress_cert_source == 'prompt':
        inp = append_message(inp, ingress_cert)
    if ingress_key_source == 'prompt':
        inp = append_message(inp, ingress_key)

    # Chart
    if chart_repo_name_source=='prompt':
        inp = append_message(inp, chart_repo_name)
    if chart_repo_url_source=='prompt':
        inp = append_message(inp, chart_repo_url)
    if chart_user_source=='prompt':
        inp = append_message(inp, chart_user)
    if chart_pass:
        inp = append_message(inp, f"{chart_pass}\n{chart_pass}")


    # License
    inp = input_secret(inp, lic_sec_exists, lic_sec_is_valid, lic_overwrite, lic)

    # Image
    inp = append_message(inp, repo)
    inp = input_secret(inp, image_sec_exists, image_sec_is_valid,
        image_sec_overwrite, input_image(user, use_existing_creds))

    # Client
    if client_sec_exists and not client_sec_is_valid:
        inp = append_message(inp, client_overwrite)

    # Keycloak passwords
    if deploy_keycloak:
        inp = input_secret(inp, kc_secret_exists, kc_secret_is_valid, kc_secret_overwrite, f'{test_pass}\n{test_pass}\n{test_pass}\n{test_pass}')
        inp = input_secret(inp, pg_secret_exists, pg_secret_is_valid, pg_secret_overwrite, f'{test_pass}\n{test_pass}\n{test_pass}\n{test_pass}')

    # values
    if values_exist and overwrite_values == 'y':
        inp = append_message(inp, overwrite_values)
    elif values_exist:
        inp = append_message(inp, f'{overwrite_values}\n{output_file}_new')

    # install config
    if install_config_exists:
        inp = append_message(inp, overwrite_install_config)

    return inp


def cli_output(
    verb,
    cli_config,
    output_file = None,
    incluster = False,
    provide_ingress_cert = 'n',
    ingress_cert = None,
    ingress_cert_source = 'prompt',
    ingress_key = None,
    ingress_key_source = 'prompt',
    lic = test_lic_file,
    lic_sec_exists = False,
    lic_sec_is_valid = True,
    lic_sec_overwrite = 'y',
    repo = test_image_repo,
    user = test_user,
    image_sec_exists = False,
    image_sec_is_valid = True,
    image_sec_overwrite = 'y',
    use_existing_creds = 'n',
    client_sec_exists = False,
    client_sec_is_valid = True,
    client_sec_overwrite = 'y',
    kc_secret_exists = False,
    kc_secret_is_valid = True,
    kc_secret_overwrite = 'y',
    pg_secret_exists = False,
    pg_secret_is_valid = True,
    pg_secret_overwrite = 'y',
    gui_secret_source = 'generated',
    operator_secret_source = 'generated',
    deploy_keycloak=True,
    values_exist = False,
    overwrite_values = 'y',
    install_config_exists = False,
    overwrite_install_config = 'n',
    hostname = test_host,
    hostname_source = 'config',
    chart_repo_existing=None,
    chart_repo_name=test_chart_repo_name,
    chart_repo_name_source='prompt',
    chart_repo_url=test_chart_repo_url,
    chart_repo_url_source='prompt',
    chart_user=test_user,
    chart_user_source='prompt',
    chart_pass=test_pass
):

    # TODO: implement full run and upgrade support properly
    if verb == 'run':
        if not lic_sec_exists:
            return output_run_required_secrets_dont_exist()
        elif not lic_sec_is_valid:
            return output_run_required_secrets_invalid()
    elif verb == 'upgrade':
        if not lic_sec_exists:
            return output_upgrade_required_secrets_dont_exist()
        elif not lic_sec_is_valid:
            return output_upgrade_required_secrets_invalid()

    ingress = output_ingress(hostname, hostname_source, provide_ingress_cert, ingress_cert, ingress_cert_source, ingress_key, ingress_key_source, cli_config)
    chart = output_chart(chart_repo_existing, chart_repo_name, chart_repo_name_source, chart_repo_url, chart_repo_url_source, chart_user, chart_user_source, chart_pass, cli_config)
    license = output_license(lic, lic_sec_exists, lic_sec_is_valid, lic_sec_overwrite)
    image = output_image(repo, user, image_sec_exists, image_sec_is_valid, use_existing_creds, image_sec_overwrite)
    client = output_client(client_sec_exists, client_sec_is_valid, client_sec_overwrite)
    keycloak = output_keycloak(cli_config, deploy_keycloak, kc_secret_exists, kc_secret_is_valid, kc_secret_overwrite,
        pg_secret_exists, pg_secret_is_valid, pg_secret_overwrite, gui_secret_source, operator_secret_source)
    values = output_values_file(output_file, values_exist, overwrite_values)
    install_config = output_install_config(install_config_exists, overwrite_install_config)

    out = output_setup_start(incluster)
    out = f'{out}\nUsing install.configSecret from embedded default values: kxi-install-config'
    out = f'{out}\n{ingress}\n{chart}\n{license}\n{image}'
    out = f'{out}\n{client}\n{keycloak}{values}\n{install_config}'

    if overwrite_values == 'n':
        output_file = f'{output_file}_new'
    out = f'{out}\n{output_setup_end(output_file)}'

    return out



def output_secret(name, exists, is_valid, prompt, overwrite):
    """Prints expected output for a secret based on existence and validatity"""
    if not exists:
        created = phrases.secret_created.format(name=name)
        if prompt:
            str = f'{prompt}\n{created}'
        else:
            str = created
    elif not is_valid:
        invalid = f'{phrases.secret_exist_invalid.format(name=name)} [y/N]: {overwrite}'
        overwriting = phrases.secret_overwriting.format(name=name)
        updated = phrases.secret_updated.format(name=name)
        if prompt:
            str = f'{invalid}\n{overwriting}\n{prompt}\n{updated}'
        else:
            str = f'{invalid}\n{overwriting}\n{updated}'
    else:
        str = phrases.secret_use_existing.format(name=name)
    return str


def output_option(out, name, source, cli_config, prompt_message, default, value):
    str = ''
    if not value:
        return out
    if source == 'prompt':
        if default:
            prompt_message = prompt_message + f' [{default}]'
        str = prompt_message + f': {value}'
    elif source == 'config':
        str = f'Using {name} from config file {cli_config}: {value}'
    elif source == 'command-line':
        str = f'Using {name} from command line option: {value}'
    return append_message(out, str)


def output_setup_start(incluster):
    out = phrases.header_setup
    if incluster:
        cluster_message = f'Running in namespace {test_namespace} in-cluster'
    else:
        cluster_message = phrases.ns_and_cluster.format(namespace=test_namespace, cluster=test_cluster)
    return append_message(out, cluster_message)


def output_chart(chart_repo_existing, chart_repo_name, chart_repo_name_source, chart_repo_url, chart_repo_url_source, chart_user, chart_user_source, chart_pass, cli_config):
    out = phrases.header_chart
    out = output_option(out, 'chart.repo.name', chart_repo_name_source, cli_config, phrases.chart_repo, common.get_default_val('chart.repo.name'), chart_repo_name)
    if chart_repo_existing:
        out = append_message(out, f'Using existing helm repo {chart_repo_existing}')
    else:
        out = output_option(out, 'chart.repo.url', chart_repo_url_source, cli_config, phrases.chart_repo_url, common.get_default_val('chart.repo.url'), chart_repo_url)
        out = output_option(out, 'chart.repo.username', chart_user_source, cli_config, phrases.chart_user, common.get_default_val('chart.repo.user'), chart_user)
        if chart_pass: out = append_message(out, f"{phrases.chart_password}: \n{phrases.password_reenter}: ")
    return out


def output_license(license, exists, is_valid, overwrite):
    secret_name = common.get_default_val('license.secret')
    header = phrases.header_license
    prompt = f'{phrases.license_entry}: {license}'

    return f"""{header}
{output_secret(secret_name, exists, is_valid, prompt, overwrite)}"""


def output_client(exists, is_valid, overwrite):
    secret_name = common.get_default_val('client.cert.secret')
    header = phrases.header_client_cert
    prompt = ''
    return f"""{header}
{output_secret(secret_name, exists, is_valid, prompt, overwrite)}"""


def output_keycloak_secret(exists, is_valid, overwrite):
    secret_name = common.get_default_val('keycloak.secret')
    prompt = f'{phrases.keycloak_admin}: \n{phrases.password_reenter}: \n{phrases.keycloak_manage}: \n{phrases.password_reenter}: '
    return f'{output_secret(secret_name, exists, is_valid, prompt, overwrite)}'


def output_postgresql_secret(exists, is_valid, overwrite):
    secret_name = common.get_default_val('keycloak.postgresqlSecret')
    prompt = f'{phrases.postgresql_postgres}: \n{phrases.password_reenter}: \n{phrases.postgresql_user}: \n{phrases.password_reenter}: '
    return f'{output_secret(secret_name, exists, is_valid, prompt, overwrite)}'


def output_image(repo, user, exists, is_valid, use_existing_creds, overwrite):
    secret_name = common.get_default_val('image.pullSecret')
    header = phrases.header_image
    post_header = f'{phrases.image_repo} [registry.dl.kx.com]: {repo}'
    creds = f'{phrases.image_creds.format(user=test_user, repo=test_image_repo, config=test_docker_config_json)} [y/N]: {use_existing_creds}'
    if use_existing_creds == 'y':
        prompt = creds
    else:
        enter_user = f'{phrases.image_user.format(repo=repo)}: {user}'
        password = f'{phrases.image_password.format(user=user)}: '
        prompt = f'{creds}\n{enter_user}\n{password}\n{phrases.password_reenter}: '

    return f"""{header}
{post_header}
{output_secret(secret_name, exists, is_valid, prompt, overwrite)}"""


def output_ingress(hostname, hostname_source, provide_ingress_cert, ingress_cert, ingress_cert_source, ingress_key, ingress_key_source, cli_config):
    str = phrases.header_ingress
    str = output_option(str, 'hostname', hostname_source, cli_config, phrases.hostname_entry, test_host, hostname)
    str = output_option(str, 'ingress.cert', ingress_cert_source, cli_config, phrases.ingress_tls_cert, '', ingress_cert)
    str = output_option(str, 'ingress.key', ingress_key_source, cli_config, phrases.ingress_tls_key, '', ingress_key)
    if provide_ingress_cert == 'y':
        secret_name = common.get_default_val('ingress.cert.secret')
        str = append_message(str, phrases.secret_created.format(name=secret_name))
    elif provide_ingress_cert == 'n':
        str = append_message(str, phrases.ingress_lets_encrypt)
    
    return str


def output_install_config(exists, overwrite):
    secret_name = common.get_default_val('install.configSecret')
    prompt = f'{phrases.secret_exist.format(name=secret_name)} [y/N]: {overwrite}'

    if not exists:
        str = phrases.secret_created.format(name=secret_name)
    elif overwrite == 'y':
        overwriting = phrases.secret_overwriting.format(name=secret_name)
        updated = phrases.secret_updated.format(name=secret_name)
        str = f'{prompt}\n{overwriting}\n{updated}'
    else:
        str = prompt

    return str

def output_keycloak_clients(cli_config, gui_secret_source, operator_secret_source):
    gui = output_client_prompt(cli_config, 'guiClientSecret', gui_secret_source)
    op = output_client_prompt(cli_config, 'operatorClientSecret', operator_secret_source)
    return f'{gui}\n{op}'


def output_client_prompt(cli_config, secret_name, secret_source):
    source = ''
    persist = ''
    if secret_source == 'command-line':
        source =  f'Using {secret_name} from command line option'
    elif secret_source == 'config': 
        source = f'Using {secret_name} from config file {cli_config}'
    if not secret_source == 'config':
        persist = phrases.persist_config.format(name=secret_name, file=cli_config)
    return append_message(source, persist)


def output_keycloak(
    cli_config,
    deploy,
    kc_secret_exists,
    kc_secret_is_valid,
    kc_secret_overwrite,
    pg_secret_exists,
    pg_secret_is_valid,
    pg_secret_overwrite,
    gui_secret_source,
    operator_secret_source
):
    header = phrases.header_keycloak
    kc_secret = output_keycloak_secret(kc_secret_exists, kc_secret_is_valid, kc_secret_overwrite)
    pg_secret = output_postgresql_secret(pg_secret_exists, pg_secret_is_valid, pg_secret_overwrite)
    clients = output_keycloak_clients(cli_config, gui_secret_source, operator_secret_source)

    if deploy:
        str = f'{header}\n{kc_secret}\n{pg_secret}\n{clients}'
    else:
        str = f'{header}\n{clients}'
    return str

def output_values_file(output_file, exists, overwrite):
    if exists:
        str = f'\n{phrases.values_file_overwrite.format(output_file=output_file)} [y/N]: {overwrite}'
        if overwrite == 'n':
            new_save_path = f'{phrases.values_save_path}: {output_file}_new'
            str = append_message(str, new_save_path)
    else:
        str = ''
    return str

def output_setup_end(output_file):
    return f"""{phrases.footer_setup}
{phrases.values_file_saved.format(output_file=output_file)}
"""

def input_secret(inp, exists, is_valid, overwrite, data):
    # if it doesn't exist just pass in the data
    if not exists:
        inp = append_message(inp, data)
    # if it exists and is invalid, pass in the data only if you want to overwrite
    elif exists and not is_valid:
        inp = input_provide_data(inp, overwrite, data)
    return inp

def input_provide_data(inp, prompt_answer, data):
    if prompt_answer == 'y':
        inp = append_message(inp, f'{prompt_answer}\n{data}')
    else:
        inp = append_message(inp, prompt_answer)
    return inp

def input_image(user, use_existing_creds):
    str = use_existing_creds
    if use_existing_creds == 'n':
        str = f'{str}\n{user}\n{test_pass}\n{test_pass}'
    return str


def output_run_required_secrets_invalid():
    return f"""Validating values...
error=Required secret kxi-license has an invalid format, expected type Opaque and keys ('license',)
error=Required secret kxi-certificate has an invalid format, expected type kubernetes.io/tls and keys ('tls.crt', 'tls.key')
error=Required secret kxi-nexus-pull-secret has an invalid format, expected type kubernetes.io/dockerconfigjson and keys ('.dockerconfigjson',)
error=Required secret kxi-keycloak has an invalid format, expected type Opaque and keys ('admin-password', 'management-password')
error=Required secret kxi-postgresql has an invalid format, expected type Opaque and keys ('postgres-password', 'password')
Error: {phrases.values_validation_fail}
"""

def output_run_required_secrets_dont_exist():
    return f"""Validating values...
error=Required secret kxi-license does not exist
error=Required secret kxi-certificate does not exist
error=Required secret kxi-nexus-pull-secret does not exist
error=Required secret kxi-keycloak does not exist
error=Required secret kxi-postgresql does not exist
Error: {phrases.values_validation_fail}
"""

def output_upgrade_required_secrets_invalid():
    return f'{phrases.header_upgrade}\n{output_run_required_secrets_invalid()}'


def output_upgrade_required_secrets_dont_exist():
    return f'{phrases.header_upgrade}\n{output_run_required_secrets_dont_exist()}'
