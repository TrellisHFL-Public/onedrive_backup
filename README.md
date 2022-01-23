# OneDrive Backup
This script downloads Onedrive data using rclone, compresses it, and (optionally) uploads it to S3. A simple Docker container is included with the necessary dependencies.

If the site_name argument is provided, the script will download all data from all drives in that SharePoint site. If it is not provided, it will download all data from all drives in all sharepoint sites across the configured tenant. 

If the split_folders argument is provided, the script will recursive split drives and folders based on the "large_dir_size_mb" and onedrive metrics to reduce the processing "chunk" size and the resulting .tar.gz file sizes. This works around access token time outs and simplifies file restoration.

If the "max_age" argument is provided, the script will perform an "incremental" backup instead of a "full" backup of the data. Only files newer than the age provided will be downloaded. This enables a common backup strategy of frequent incremental backups and less frequent full backups (e.g. incremental each day and full every week).

Files will be saved into S3 with the following naming scheme after replacing all characters that do not match the following regex with underscores: `[^0-9a-zA-Z\-_ ]`
```
{s3_object_prefix}/{current date:%Y-%m-%d}/{site_name}/{site_name}_{drive_name}-{folder or "root"}.tar.gz
```
or if max_age is provided:
```
{s3_object_prefix}/{current date:%Y-%m-%d}_max_age_{max_age}/{site_name}/{site_name}_{drive_name}-{folder or "root"}.tar.gz
```

Note: This script is not suitable for AWS Lambda because download times can easily surpass the 15 minute time limit. Even a 3 GB folder was large enough to fail in our testing.

## Command Arguments
* data_dir: Local folder for saving onedrive folders less than "large_dir_size_mb" in size (Default ".")
* large_data_dir: Local folder for saving onedrive folders greater than "large_dir_size_mb" in size (Default ".")
* site_name: Sharepoint site name to download. If not set, all drives from all sites in the tenant will be downloaded. (Default "")
* split_folders: if not set, drives will be downloaded all at once and compressed as a single archive. If set, drives and folders will be recursively split into subfolders based on the "large_dir_size_mb"
* max_age: [rclone config](https://rclone.org/filtering/#max-age-don-t-transfer-any-file-older-than-this) configuration to ignore files older than a certain age. Note that split_folders is not able to respect this parameter, so it will split folders based on the total data of any age inside the folder
* max_parallelism: Max folders to download at once (Default 10)
* retry_count: Number of times to retry folder downloads after a failure occurs (Default 2)
* large_dir_size_mb: MB threshold drive or folder size that indicates the program should download subfolders individually (acts recursively). Also separates saving data into "large_data_dir" instead of "data_dir" for indivisable folders. (Default 100)
* s3_bucket: If configured, data from data_dir and large_data_dir will be uploaded to the bucket and deleted from the local filesystem
* s3_object_prefix: Prefix (aka folder) to use in S3 for all data (Default "onedrive_backup")

## Environment Variables
* ONEDRIVE_TENANT_ID
* ONEDRIVE_CLIENT_ID
* ONEDRIVE_CLIENT_SECRET (required if ONEDRIVE_CLIENT_SECRET_ASM_ID is not set)
* ONEDRIVE_CLIENT_SECRET_ASM_ID (AWS Secrets Manager secret id. Script will retrieve the secret at runtime. Required if ONEDRIVE_CLIENT_SECRET is not set)
* AWS_ACCESS_KEY_ID (Required for ASM and S3)
* AWS_SECRET_ACCESS_KEY (Required for ASM and S3)

## Examples
```
#Full backup
python3 onedrive_backup.py --data_dir /main_data_dir/main_files --large_data_dir /main_data_dir/big_files --split_folders

#Incremental backup
python3 onedrive_backup.py --data_dir /main_data_dir/main_files --large_data_dir /main_data_dir/big_files --max_age 7d

#Backup specific site using docker
docker run --rm -it -e ONEDRIVE_TENANT_ID=abc -e ONEDRIVE_CLIENT_ID=abc -e ONEDRIVE_CLIENT_SECRET=abc -v $(pwd):/data onedrive_backup --data_dir /data --large_data_dir /data --site_name Test
```