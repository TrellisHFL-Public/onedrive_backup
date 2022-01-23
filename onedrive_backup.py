'''
 Copyright (c) 2022 Trellis Housing Finance Limited All Rights Reserved.

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

 http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
'''

import argparse
import asyncio
import boto3
import datetime
import json
import os
import time
import aiohttp
import re

class OneDriveRepository():
    async def create(self, data_dir, large_data_dir=None):
        self.data_dir = data_dir
        self.large_data_dir = large_data_dir or data_dir
        self.tenant_domain_name = os.getenv("ONEDRIVE_TENANT_ID", None)
        self.clientID = os.getenv("ONEDRIVE_CLIENT_ID", None)
        self.client_secret_asm_id = os.getenv("ONEDRIVE_CLIENT_SECRET_ASM_ID", None)
        self.client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET", None)
        
        if self.clientID is None or self.tenant_domain_name  is None or (self.client_secret_asm_id is None and self.client_secret is None):
            print("Onedrive misconfigured")
            return

        if self.client_secret is None:
            session = boto3.session.Session()
            client = session.client(
                service_name='secretsmanager',
                region_name=os.getenv("AWS_REGION","")
            )
            self.client_secret = client.get_secret_value(
                SecretId=self.client_secret_asm_id
            )['SecretString']
        
        #login
        self.session = aiohttp.ClientSession()
        await self.update_access_token()

        return self
    async def update_access_token(self):
        data = {
            'grant_type':"client_credentials", 
            'resource':"https://graph.microsoft.com", 
            'client_id':self.clientID, 
            'client_secret':self.client_secret
        } 
        async with self.session.post(url="https://login.windows.net/{}/oauth2/token?api-version=1.0".format(self.tenant_domain_name), data=data) as resp:
            text = await resp.text()
            j = json.loads(text)
            self.access_token = j["access_token"]

    async def close(self):
        await self.session.close()

    async def _get_request(self, url):
        headers={
            'Authorization': "Bearer " + self.access_token,
            'Content-Type': 'application/json',
            "Accept-Encoding":"deflate"
        }
        
        async with self.session.get(url="https://graph.microsoft.com/v1.0{}".format(url), headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200 and resp.status != 201:
                raise Exception("error: {} {}".format(resp.status, text))
        return json.loads(text)
    
    async def get_drives(self, site_name=''):
        sites = (await self._get_request("/sites?search=*"))["value"]

        if site_name != '':
            sites = [s for s in sites if s["name"] == site_name]

        drives = []
        for s in sites:           
            try:
                drive_list = (await self._get_request("/sites/{}/drives".format(s["id"])))["value"]
            except Exception as ex:
                print(ex)
                continue
            
            drives += [{
                "site_display_name": s.get("displayName"),
                "site_name": s["name"],
                "drive_name": drive_properties.get("name", ""),
                "drive_id": drive_properties["id"],
                "size_bytes": drive_properties.get("quota",{}).get("used",0)
            } for drive_properties in drive_list]

        return drives
         
    async def create_tasks_for_drive(self, base_folder, large_dir_size_mb, size_bytes, site_display_name, site_name, drive_name, drive_id, split_folders):        
        get_subfolders = split_folders and size_bytes > int(large_dir_size_mb)*1024*1024 #MB
        sub_folder_size_sum = 0
        tasks = []
        
        url = ""
        if base_folder is None:
            url = f"/drives/{drive_id}/root/children"
        else:
            url = f"/drives/{drive_id}/root:/{base_folder}:/children"
            
        children = (await self._get_request(url))["value"]

        if get_subfolders:
            for f in children:
                if "folder" in f:
                    folder_name = (base_folder or "")+"/"+f["name"]
                    sub_size_bytes = f["size"]
                    sub_folder_size_sum += sub_size_bytes
                    tasks += await self.create_tasks_for_drive(folder_name, large_dir_size_mb, sub_size_bytes, site_display_name, site_name, drive_name, drive_id, split_folders)
        
        tasks += [{
            "site_display_name": site_display_name,
            "site_name": site_name,
            "drive_name": drive_name,
            "drive_id": drive_id,
            "folder": base_folder,
            "size_bytes": size_bytes-sub_folder_size_sum,
            "get_subfolders": get_subfolders
        }]
        
        return tasks

    async def download_folder_from_drive(self, task, sema, large_dir_size_mb, retry_count, max_age):
        data_dir = self.data_dir if task["size_bytes"] < int(large_dir_size_mb)*1024*1024 else self.large_data_dir
        folder = task["folder"]
        drive_name = task["drive_name"]
        task_display_name = f"{task['site_display_name']}:{drive_name}-{folder or 'root'}"
        site_name = task["site_name"]
        drive_id = task["drive_id"]
        fs_name = f"{site_name}_{drive_name}-{folder or 'root'}"
        fs_name = re.sub("[^0-9a-zA-Z\-_ ]", "_", fs_name)
        async with sema:
            for i in range(1+int(retry_count)):
                start = time.time()
                try:
                    await self.update_access_token()
                    #generate config file
                    if not os.path.exists(data_dir+"/config"):
                        os.makedirs(data_dir+"/config")
                    with open(f"{data_dir}/config/{fs_name}.config", "w") as f:
                        f.write(f"[{fs_name}]\n")
                        f.write("type = onedrive\n")
                        f.write('token = {"access_token":"'+self.access_token+'","token_type":"Bearer"}\n')
                        f.write(f"drive_id = {drive_id}\n")
                        f.write("drive_type = documentLibrary\n")

                    if not os.path.exists(f"{data_dir}/data/{fs_name}"):
                        os.makedirs(f"{data_dir}/data/{fs_name}")
                    
                    #download data
                    copy_command = []
                    max_age_param = f"--max-age {max_age}" if max_age else ""
                    if task["get_subfolders"]: #separate task for sub folders
                        copy_command=f'rclone --config "{data_dir}/config/{fs_name}.config" {max_age_param} --max-depth 1 copy "{fs_name}:{folder or ""}" "{data_dir}/data/{fs_name}"'
                    else:
                        copy_command=f'rclone --config "{data_dir}/config/{fs_name}.config" {max_age_param} copy "{fs_name}:{folder or ""}" "{data_dir}/data/{fs_name}"'
                        
                    proc = await asyncio.create_subprocess_shell(copy_command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        if stdout:
                            print(f'[stdout]\n{stdout.decode()}')
                        if stderr:
                            print(f'[stderr]\n{stderr.decode()}')                                                                                                                                                                                                                                                  
                            raise Exception(f"rclone failed for {task_display_name}")

                    #tar gzip
                    zip_command = f'tar vcfz "../../{fs_name}.tar.gz" .'
                    zip_cwd = f"{data_dir}/data/{fs_name}"
                    proc = await asyncio.create_subprocess_shell(zip_command,cwd=zip_cwd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        if stdout:
                            print(f'[stdout]\n{stdout.decode()}')
                        if stderr:
                            print(f'[stderr]\n{stderr.decode()}')
                        raise Exception(f"tar failed for {task_display_name}")
                    
                    #clean up
                    rm_command = f'rm -r "{data_dir}/data/{fs_name}" "{data_dir}/config/{fs_name}.config"'
                    proc = await asyncio.create_subprocess_shell(rm_command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        if stdout:
                            print(f'[stdout]\n{stdout.decode()}')
                        if stderr:
                            print(f'[stderr]\n{stderr.decode()}')
                        raise Exception(f"clean up failed for {task_display_name}")
                    
                    return {
                        "site_name": site_name,
                        "archive_path": f"{data_dir}/{fs_name}.tar.gz",
                        "archive_size_bytes": os.path.getsize(f'{data_dir}/{fs_name}.tar.gz'),
                        "execution_time": int(time.time() - start)
                    }
                except Exception as ex:
                    print(f"Failed to download {task_display_name} after {int(time.time() - start)} seconds. {ex}")
                    continue
        return {
            "site_name": site_name,
            "archive_path": f"{data_dir}/{fs_name}.tar.gz",
            "execution_time": -1
        }
        
class S3Repository():
    def __init__(self, s3_bucket):
        self.s3_bucket = s3_bucket

    def upload_file(self, prefix, file_path):        
        s3 = boto3.resource('s3')

        s3.Bucket(self.s3_bucket).upload_file(file_path, prefix+"/"+os.path.basename(file_path))

async def main():
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--data_dir', type=str, default = '.')
    parser.add_argument('--large_data_dir', type=str, default = '.')
    parser.add_argument('--site_name', type=str, default = '')
    parser.add_argument('--split_folders', dest='split_folders', action='store_true')
    parser.add_argument('--max_age', type=str)
    parser.add_argument('--max_parallelism', type=int, default = 10)
    parser.add_argument('--retry_count', type=int, default = 2)
    parser.add_argument('--large_dir_size_mb', type=int, default = 100)
    parser.add_argument('--s3_bucket', type=str)
    parser.add_argument('--s3_object_prefix', type=str, default = "onedrive_backup")
    args = parser.parse_args()

    sourceRepository = await OneDriveRepository().create(args.data_dir, args.large_data_dir)
    if sourceRepository is None:
        return
    
    destinationRepository = None
    if args.s3_bucket:
        destinationRepository = S3Repository(args.s3_bucket)

    sema = asyncio.Semaphore(args.max_parallelism)

    prefix = args.s3_object_prefix+"/"+datetime.datetime.today().strftime('%Y-%m-%d')
    if args.max_age:
        prefix += f"_max_age_{args.max_age}"
        re.sub("[^0-9a-zA-Z\-_ ]", "_", prefix)

    #Start download
    start = time.time()
    drives = await sourceRepository.get_drives(args.site_name)

    max_download_time = 0
    async_tasks = []
    task_count = 0
    for d in drives:
        tasks = await sourceRepository.create_tasks_for_drive(None, args.large_dir_size_mb, d["size_bytes"], d["site_display_name"], d["site_name"], d["drive_name"], d["drive_id"], args.split_folders)
        task_count += len(tasks)
        async_tasks += [sourceRepository.download_folder_from_drive(task, sema, args.large_dir_size_mb, args.retry_count, args.max_age) for task in tasks]
    
    print(f"Starting {task_count} download tasks")
    total_archive_size_bytes = 0
    tasks_completed = 0
    for coro in asyncio.as_completed(async_tasks):
        result = await coro
        if result["execution_time"] < 0:
            raise Exception(f'{result["archive_path"]} download failed')
        elif destinationRepository is not None:      
            destinationRepository.upload_file(prefix+"/"+result["site_name"], result["archive_path"])
            os.remove(result["archive_path"])
        #else done 
        total_archive_size_bytes += result["archive_size_bytes"]
        tasks_completed += 1
        if tasks_completed % 100 == 0:
            print(f"{tasks_completed}/{task_count} tasks complete")
        #print(f'{int(result["execution_time"])} seconds to download {result["archive_path"]}({result["archive_size_mb"]}MB compressed)')
        if result["execution_time"] > max_download_time:
            max_download_time = result["execution_time"]

    await sourceRepository.close()

    end = time.time()
    print(f"Downloaded all drives in {int(end - start)} seconds. Total compressed size: {(total_archive_size_bytes/1024)/1024}MB")
    print(f"Maximum download task execution time: {max_download_time} seconds")

if __name__ == '__main__':
    asyncio.run(main())