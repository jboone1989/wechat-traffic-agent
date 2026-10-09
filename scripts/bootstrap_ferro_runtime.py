#!/usr/bin/env python3
"""Rebuild Ferro's stable environment from declared dependencies and local code."""
from pathlib import Path
import subprocess
import tomllib

ENVIRONMENT = Path('/home/ubuntu/.local/share/ferro/venv')
PROJECTS = [
    Path('/home/ubuntu/content-agent/current'),
    Path('/home/ubuntu/agent-runtime/current'),
    Path('/home/ubuntu/virtualbrain/current'),
    Path('/home/ubuntu/src/agent-network/agent-network-sdk-python'),
    Path('/home/ubuntu/vendor/FlyDrones'),
]


def main():
    if not (ENVIRONMENT / 'bin/python').exists():
        subprocess.run(['python3', '-m', 'venv', str(ENVIRONMENT)], check=True)
    python = str(ENVIRONMENT / 'bin/python')
    dependencies = []
    for project in PROJECTS:
        metadata = tomllib.loads((project / 'pyproject.toml').read_text())
        dependencies.extend(requirement for requirement in metadata['project']['dependencies']
                            if not requirement.startswith(('agent-runtime', 'agent-network-sdk', 'virtualbrain')))
    dependencies.extend([
        'pytest>=8', 'playwright>=1.55,<2', 'browser-harness==0.1.13',
        'jev-ultrafast @ git+https://github.com/browser-use/jev-ultrafast.git@1231850a0bf1a0c0341fe408ef1668dbbfdfac46',
    ])
    subprocess.run([python, '-m', 'pip', 'install', *dict.fromkeys(dependencies)], check=True)
    projects = [*PROJECTS, *(Path('/home/ubuntu/src/agent-runtime/packs') / name
                            for name in ('searxng-search', 'jina-reader'))]
    subprocess.run([python, '-m', 'pip', 'install', '--no-deps',
                    *sum((['-e', str(project)] for project in projects), [])], check=True)
    subprocess.run([python, '/home/ubuntu/agent-runtime/current/scripts/patch_browser_harness_ipc.py'], check=True)
    subprocess.run([python, '-m', 'pip', 'check'], check=True)
    alias = Path('/home/ubuntu/content-agent-venv-current')
    temporary = alias.with_name(alias.name + '.tmp')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(ENVIRONMENT)
    temporary.replace(alias)
    requirements = subprocess.run([python, '-m', 'pip', 'freeze'], check=True,
                                  capture_output=True, text=True).stdout
    (ENVIRONMENT.parent / 'requirements.txt').write_text(requirements)
    print('Ferro stable runtime is ready: ' + str(ENVIRONMENT))


if __name__ == '__main__':
    main()
