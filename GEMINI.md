# Backup GDrive Koofr Operating Rules

This file supplements the Global `GEMINI.md` with rules specific to the Rclone Bisync backup system.

## 1. Rclone Bisync Handling
- **Fatal Errors**: Recognise fatal markers (e.g., "Bisync critical error", "invalid_grant", "quotaExceeded"). Do not retry automatically if these occur.
- **Ignorable Errors**: Certain errors like "insufficientFilePermissions" or "FileBlocked" may be ignored by the helper script.

## 2. Google Drive Specifics
- **Dangling Shortcuts**: "can't read dangling shortcut" is a permanent source error. Do not treat it as a transient failure.
- **Quota**: Monitor for "Error 429" or "Error 500" which typically indicate API rate limiting or quota exhaustion.

## 3. Data Safety
- **Source Protection**: The primary goal is backing up GDrive to Koofr. Avoid any `bisync` configuration that allows unintended deletion on the GDrive source.
- **Validation**: Use the `bisync-helper.py` script to wrap rclone operations for better error classification.

## 4. Deployment
- The system runs primarily via GitHub Actions (`.github/workflows/bisync.yml`).
