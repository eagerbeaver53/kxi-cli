"""
This file should build up all phrases that are emitted
"""

# Headers
header_setup = 'kdb Insights Enterprise Configuration Setup'
header_ingress = '\nChecking ingress configuration...'
header_chart = '\nChecking chart details...'
header_license = '\nChecking license details...'
header_image = '\nChecking image repository...'
header_client_cert ='\nChecking client certificate issuer...'
header_keycloak = '\nChecking keycloak configuration...'
header_run = 'No values file provided, invoking "kxi install setup"\n'
header_upgrade = 'Upgrading kdb Insights Enterprise'

# Kubernetes
namespace = '\nPlease enter a namespace to run in'
ns_and_cluster = '\nRunning in namespace {namespace} on the cluster {cluster}'

# Config
persist_config = 'Persisting option {name} to file {file}'

# Chart
chart_repo = 'Please enter a name for the chart repository to set locally'
chart_repo_url = 'Please enter the chart repository URL to pull charts from'
chart_user = 'Please enter the username for the chart repository'
chart_password = 'Please enter the password for the chart repository (input hidden)'

# Ingress
hostname_entry = 'Please enter the hostname for the installation'
ingress_cert = 'Do you want to provide a self-managed cert for the ingress'
ingress_tls_cert = 'Please enter the path to your TLS certificate'
ingress_tls_key = 'Please enter the path to your TLS private key'
ingress_lets_encrypt = "Using Let's Encrypt TLS certificates"

# Images
image_repo = 'Please enter the image repository to pull images from'
image_creds = 'Credentials {user}@{repo} exist in {config}, do you want to use these'
image_user = 'Please enter the username for {repo}'
image_password = 'Please enter the password for {user}'

# License
license_entry = 'Please enter the path and filename of your kdb+ license'

# Keycloak
keycloak_admin = 'Please enter the Keycloak Admin password (input hidden)'
keycloak_manage = 'Please enter the Keycloak WildFly Management password (input hidden)'
postgresql_postgres = 'Please enter the Postgresql postgres password (input hidden)'
postgresql_user = 'Please enter the Postgresql user password (input hidden)'
service_account_secret = 'Do you want to set a secret for the {name} service account explicitly'
service_account_random = 'Randomly generating client secret for {name} and setting in values file, record this value for reuse during upgrade'
option_persistence = 'Persisting option {x} to file {y}'

# Secrets
secret_created = 'Secret {name} successfully created'
secret_exist_invalid = 'Secret {name} already exists but is invalid. Do you want to overwrite it?'
secret_exist = 'Secret {name} already exists. Do you want to overwrite it?'
secret_overwriting = 'Overwriting secret {name}'
secret_use_existing = 'Using existing valid secret {name}'
secret_use_existing_type = 'Please provide license type for existing secret'
secret_entry = 'Please enter the secret (input hidden)'
secret_updated = 'Secret {name} successfully updated'

secret_validation_not_exist = 'Required secret {name} does not exist'
secret_validation_invalid = 'Required secret {name} has an invalid format, expected type {type} and keys {keys}'

# Values files
values_file_overwrite = '\n{output_file} file exists. Do you want to overwrite it with a new values file?'
values_save_path = 'Please enter the path to write the values file for the install'
values_file_saved = '\nHelm values file for installation saved in {output_file}\n'
values_validating = 'Validating values...'
values_validation_fail = 'Validation failed, run "kxi install setup" to fix'
values_filepath_missing = 'Please provide a values file with --filepath'

# Footers
footer_setup = '\nConfiguration saved, kdb Insights Enterprise is now ready for install'

# Upgrade
upgrade_skip_to_install = 'kdb Insights Enterprise is not deployed. Skipping to install'
upgrade_asm_backup = '\nBacking up assemblies'
upgrade_asm_teardown = '\nTearing down assemblies'
upgrade_asm_reapply = '\nReapplying assemblies'
upgrade_asm_persist = 'Assembly data will be persisted and state will be recovered post-upgrade'
upgrade_insights= '\nUpgrading insights'
upgrade_complete = '\nUpgrade to version {version} complete'
rollback_asm_persist = 'Assembly data will be persisted and state will be recovered post-rollback'
check_installed = 'kdb Insights Enterprise is already installed with version {insights_installed_version}'

# Passwords
password_reenter = 'Re-enter to confirm (input hidden)'
password_no_match = 'Entered values did not match'

# Common
hostname_prefix = 'Failed to request access token, hostname missing protocol - specify http or https [{hostname}]'
hostname_none = 'Hostname is empty'

# Users
missing_user_argument = "'username' must be provided as a keyword argument"

# Rollback
rollback_insights = 'Rolling Insights back to version {insights_version} and revision {revision}. Operator version remaining on {operator_version}.'
rollback_insights_operator = 'Rolling Insights back to version {insights_version} and revision {revision}.\nRolling operator back to version {operator_version} and revision {operator_revision}.'
rollback_start = '\nRolling back Insights'

# Helm
helm_get_values_fail = 'Failed to get values for release {release} in namespace {namespace}, error from helm is "{helm_error}"'

# Management Service
install_management = '\nInstall complete for the KXI Management Service'
upgrade_management = '\nUpgrading KXI Management Service'
management_install = '\nInstalling {release_name} to version {version}'
management_check_installed = 'kxi-management-service is already installed with version {insights_installed_version}'