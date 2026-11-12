# Publishing SetScout to GitHub

## Fastest method — GitHub CLI (recommended)

1. Install [Git for Windows](https://git-scm.com/download/win) and the [GitHub CLI](https://cli.github.com/) if you do not already have them.
2. Extract this project folder and open PowerShell or Command Prompt inside it.
3. Sign in (only needs doing once):

   ```powershell
   gh auth login
   ```

4. Create a private repository and push the code:

   ```powershell
   git init -b main
   git add .
   git commit -m "Initial SetScout v3 release"
   gh repo create SetScout --private --source . --remote origin --push --description "Steam Achievement Sets discovery and limited-time achievement tracker"
   ```

If Git asks for your name/email, set them with `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"` and repeat the commit command.

The new repository will be visible at `https://github.com/YOUR_USERNAME/SetScout`. Change its visibility in GitHub **Settings → General → Danger Zone → Change repository visibility** if you want it to be public.

## Without GitHub CLI

1. On [GitHub](https://github.com/new), create a **private** repository named `SetScout` and **do not** initialize it with README or .gitignore.
2. From this folder, run:

   ```powershell
   git init -b main
   git add .
   git commit -m "Initial SetScout v3 release"
   git remote add origin https://github.com/YOUR_USERNAME/SetScout.git
   git push -u origin main
   ```

GitHub will ask you to sign in using an approved Git credential helper or personal access token; do **not** paste a password or token into chat.

## Before publishing

- Check `git status --short` and `git ls-files` to confirm `games.json` is **not** listed.
- Decide whether the repo should be private or public and whether to add an open-source license.
- A GitHub repository is source hosting; it does not automatically deploy the local Python API as a hosted website.
