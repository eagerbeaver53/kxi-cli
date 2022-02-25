
# Contributing

When contributing to this repository, please first discuss the change you wish to make via issue,
email, or any other method with the owners of this repository before making a change.


## Coding standards

TODO


## Code layout

This section will give a brief layout of the code.

The first place to look at is `setup.py`. 
It describes the repository;

- metadata (name, version)
- dependencies & versions
- entrypoint script - **this is the key one**
  - points to the `cli` function in the `kxicli/main.py` script

The main CLI source code is all contained in the `kxicli` directory.

- `__init__.py` - Stub file to for pip/Python to treat directory as a module


## Developing

- Create a branch corresponding to the JIRA ticket for your change
- Create a virtualenv and install the project in editable mode
- For convenience of working outside the _venv_, you can add the CLI to your path and deactivate the virtualenv

```bash
git checkout -b KXI-XXXX

virtualenv venv
source venv/bin/activate
pip install -e .

deactivate
export PATH=$(pwd)/venv/bin:${PATH}
```

- Any changes to the source files will automatically be available in the CLI runtime


## Testing locally

To verify the existing tests work as expected and to add further test coverage, you can run the tests manually
as below.

```bash
pip install -r kxicli/tests/requirements.txt
export KUBECONFIG=$(pwd)/kxicli/tests/files/test-kube-config
pytest
```