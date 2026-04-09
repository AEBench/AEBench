# Artifact Instructions

This case validates that a minimal package installation succeeds.

## Steps

1. Update the package index:

   ```
   apt-get update -y
   ```

2. Install the `zip` utility:

   ```
   apt-get install -y zip
   ```

3. Confirm the binary is available:

   ```
   zip --version
   ```

The expected output is a version line from zip. No files are written to disk.
