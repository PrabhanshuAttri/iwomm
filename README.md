# Fedora Workstation Setup

Ansible setup for a single Fedora workstation.

The main playbook is `workstation.yml`. It installs base packages, updates the system, configures zsh with Oh My Zsh and Powerlevel10k, copies local dotfiles and fonts, updates Flatpak apps, and runs basic DNF cleanup.

## Files

```text
ansible.cfg       Ansible defaults for this repo
inventory.ini     Local workstation inventory
packages.yml      Package lists and setup toggles
workstation.yml   Main Fedora workstation playbook
setup/configs/    Shell config files copied into the user home
setup/fonts/      Fonts installed into ~/.local/share/fonts
```

## Install Ansible

```bash
sudo dnf install -y ansible
```

## Provision A New System

On a fresh Fedora system, install the prerequisites, clone this repo, and preview the setup:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash
```

Apply the setup instead of previewing it:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash -s -- --apply
```

Pick a playbook from the repo:

```bash
curl -fsSL https://raw.githubusercontent.com/PrabhanshuAttri/iwomm/main/provision.sh | bash -s -- --playbook workstation.yml
```

The provision script clones the repo into `~/git/iwomm` by default. Override that with:

```bash
TARGET_DIR=~/git/dotfiles bash provision.sh
```

## Preview

Run a dry-run before applying changes:

```bash
ansible-playbook workstation.yml --check --diff --ask-become-pass
```

`--ask-become-pass` asks for your sudo password so Ansible can run system tasks like `dnf install`.

## Apply

```bash
ansible-playbook workstation.yml --ask-become-pass
```

## Customize

Edit `packages.yml`.

Use `dnf_packages` for Fedora packages:

```yaml
dnf_packages:
  - git
  - vim
  - tmux
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
install_oh_my_zsh: true
install_powerlevel10k: true
set_zsh_as_default_shell: false
configure_git: true
update_flatpak: true
dnf_autoremove: true
dnf_clean: true
```

Keep `set_zsh_as_default_shell` as `false` unless you want Ansible to change your login shell.

## Local Assets

Shell configs are copied with backups:

```text
setup/configs/zshrc       -> ~/.zshrc
setup/configs/p10k.zsh    -> ~/.p10k.zsh
setup/configs/bash_profile -> ~/.bash_profile
```

Fonts from `setup/fonts/` are copied into:

```text
~/.local/share/fonts
```

## Commit

For the Ansible setup, commit:

```bash
git add README.md provision.sh ansible.cfg inventory.ini packages.yml workstation.yml setup
```
