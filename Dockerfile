# Copyright (c) 2022 Trellis Housing Finance Limited All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

FROM python:3.8

RUN curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip && unzip rclone-current-linux-amd64.zip && mv rclone-*-linux-amd64/rclone /bin/ && rm -rf rclone*
RUN apt install -y tar gzip

WORKDIR /code
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY onedrive_backup.py .

ENTRYPOINT [ "python", "./onedrive_backup.py" ] 