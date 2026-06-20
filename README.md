# Pipboy Workstation Setup

Ansible setup for a single Fedora or Ubuntu workstation.

The playbooks install base packages, update the system, configure zsh with Oh My Zsh and Powerlevel10k, copy local dotfiles and fonts, update Flatpak apps, and run basic package cleanup.

## Playbooks

```text
fedora-pipboy-workstation.yml    Fedora workstation setup
ubuntu-pipboy-workstation.yml    Ubuntu workstation setup
```

Both playbooks use the shared settings in `packages.yml`.

## Files

```text
ansible.cfg       Ansible defaults for this repo
inventory.ini     Local workstation inventory
packages.yml      Shared package groups and setup toggles
provision.sh      Bootstrap helper for fresh systems
setup/configs/    Shell config files copied into the user home
setup/fonts/      Fonts installed into ~/.local/share/fonts
```

## Install Ansible

Fedora:

```bash
sudo dnf install -y ansible
```

Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ansible
```

## Provision A New System

On a fresh Fedora or Ubuntu system, this installs prerequisites, clones the repo, and previews the detected playbook:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash
```

Apply the setup instead of previewing it:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash -s -- --apply
```

`provision.sh` auto-selects `fedora-pipboy-workstation.yml` on Fedora and `ubuntu-pipboy-workstation.yml` on Ubuntu. Pick a playbook manually with:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash -s -- --playbook fedora-pipboy-workstation.yml
```

The provision script clones the repo into `~/git/iwomm` by default. Override that with:

```bash
TARGET_DIR=~/git/dotfiles bash provision.sh
```

## Preview

Run a dry-run before applying changes.

Fedora:

```bash
ansible-playbook fedora-pipboy-workstation.yml --check --diff
```

Ubuntu:

```bash
ansible-playbook ubuntu-pipboy-workstation.yml --check --diff
```

Ansible asks for your sudo password before starting so system package tasks and
fact gathering can elevate without timing out. When the bootstrap is invoked
through `curl | bash`, `provision.sh` reads the password from the controlling
terminal rather than the curl pipe. The configured verbosity also shows command
arguments and their captured standard output and error streams.

On Ubuntu 26.04, the Ubuntu playbook uses `/usr/bin/sudo.ws` for Ansible
privilege escalation. This avoids the `sudo-rs` prompt incompatibility without
changing the system-wide `sudo` alternative.

## Apply

Fedora:

```bash
ansible-playbook fedora-pipboy-workstation.yml
```

Ubuntu:

```bash
ansible-playbook ubuntu-pipboy-workstation.yml
```

## Customize

Edit `packages.yml`.

Use `packages.common` for package names shared by both systems. Put distro-specific package names under `packages.fedora` or `packages.ubuntu`:

```yaml
packages:
  common:
    - git
    - vim
    - tmux
  fedora:
    - clamav-update
  ubuntu:
    - clamav-freshclam
```

Docker packages are distro-specific and controlled by `install_docker`:

```yaml
install_docker: true
packages:
  docker_fedora:
    - docker-compose
    - moby-engine
  docker_ubuntu:
    - docker-compose-v2
    - docker.io
```

Use `flatpak_packages` for Flathub app IDs:

```yaml
flatpak_packages:
  - md.obsidian.Obsidian
```

Useful toggles:

```yaml
copy_fonts: true
copy_shell_configs: true
install_vim_plugins: true
set_vim_as_default_editor: true
install_docker: true
config_files:
  - src: zshrc
    dest: .zshrc
  - src: p10k.zsh
    dest: .p10k.zsh
install_oh_my_zsh: true
install_powerlevel10k: true
set_zsh_as_default_shell: true
configure_git: true
update_flatpak: true
package_autoremove: true
package_clean: true
zsh_startup_command: fortune | cowsay -f tux | lolcat -f
```

Set `set_zsh_as_default_shell` to `false` if you do not want Ansible to change your login shell.

When `install_vim_plugins` is enabled, the playbooks install Vundle and then
install every plugin declared in the deployed `~/.vimrc`.

When `set_vim_as_default_editor` is enabled, login shells use Vim through the
`EDITOR` and `VISUAL` environment variables. Ubuntu's system `editor`
alternative is also switched from Nano to Vim.

When Docker is installed, its service is enabled and the workstation user is
added to the `docker` group. Log out and back in before using Docker without
`sudo`.

## Local Assets

Config files are copied with backups. The list is controlled by `config_files` in `packages.yml`:

```text
setup/configs/zshrc       -> ~/.zshrc
setup/configs/p10k.zsh    -> ~/.p10k.zsh
setup/configs/bash_profile -> ~/.bash_profile
setup/configs/tmux.conf   -> ~/.tmux.conf
setup/configs/vimrc       -> ~/.vimrc
```

Fonts from `setup/fonts/` are copied into:

```text
~/.local/share/fonts
```

## Commit

For the Ansible setup, commit:

```bash
git add README.md provision.sh ansible.cfg inventory.ini packages.yml fedora-pipboy-workstation.yml ubuntu-pipboy-workstation.yml setup
```
