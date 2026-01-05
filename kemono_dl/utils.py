import hashlib
import re
import os
import json
import datetime
import shutil
import zipfile
import rarfile
import py7zr
from pathlib import Path

from requests import Session


def get_sha256_hash(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def compute_sha256(text: str) -> str:
    sha256_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return sha256_hash


def format_bytes(size) -> str:
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def get_sha256_url_content(session: Session, url: str, chunk_size: int = 8192):
    sha256 = hashlib.sha256()
    with session.get(url, stream=True) as response:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                sha256.update(chunk)
    return sha256.hexdigest()


# --- Legacy Compatibility Functions (Ported from old src/helper.py) ---
# 为了保证和旧版脚本生成的路径完全一致，必须使用旧版的清理逻辑

def _clean_folder_name(folder_name: str) -> str:
    """Old helper.py: clean_folder_name"""
    if not folder_name.rstrip():
        folder_name = '_'
    # 旧版逻辑：替换非法字符以及末尾的点
    name_clean = re.sub(r'[\x00-\x1f\\/:\"*?<>\|]|\.$', '_', folder_name.rstrip())[:248]
    # 旧版逻辑：字节长度限制
    while len(name_clean.encode('utf-8', 'replace')) > 255:
        name_clean = name_clean[:-1]
    return name_clean

def _clean_file_name(file_name: str) -> str:
    """Old helper.py: clean_file_name"""
    if not file_name:
        file_name = '_'
    # 旧版逻辑：不替换末尾的点，因为要保留扩展名
    file_name = re.sub(r'[\x00-\x1f\\/:\"*?<>\|]', '_', file_name)
    file_name, file_extension = os.path.splitext(file_name)
    
    # 旧版逻辑：保留扩展名，截断文件名部分
    name_limit = 255 - len(file_extension) - 5 # -5 for .part safety
    name_clean = file_name[:name_limit] + file_extension
    while len(name_clean.encode('utf-8', 'replace')) > 250:
        name_limit -= 1
        name_clean = file_name[:name_limit] + file_extension
    return name_clean

def generate_file_path(
    base_path: str,
    output_template: str,
    template_variables: dict,
    restrict_names: bool = False,
    replacement: str = "_",
) -> str:
    path_segments = []
    try:
        # 使用正则拆分路径，以便分别处理文件夹和文件名
        raw_segments = re.split(r"[\\/]", output_template)
        
        for index, path_segment in enumerate(raw_segments):
            # 格式化变量
            formatted = path_segment.format_map(template_variables)
            
            # 关键判断：如果是最后一段，视为文件名，使用 _clean_file_name
            # 其他段视为文件夹，使用 _clean_folder_name
            if index == len(raw_segments) - 1:
                cleaned = _clean_file_name(formatted)
            else:
                cleaned = _clean_folder_name(formatted)
                
            path_segments.append(cleaned)
            
    except KeyError as e:
        missing_key = e.args[0]
        raise ValueError(f"[Error] Missing template key: '{missing_key}'.")

    path = Path(*path_segments)

    if not path.is_absolute():
        path = Path(base_path) / path

    if not path.is_absolute():
        path = Path.cwd() / path

    if restrict_names:
        # 如果开启了 ascii 限制，再做一次过滤
        path = Path(re.sub(r"[^\x20-\x7E]", replacement, str(path)))

    return str(path)


# --- NAS Feature: Auto Extract Utilities ---

def extract_archive(archive_path: str, hash_value: str, delete_extracted_types: list = [], is_new_download: bool = False) -> bool:
    """解压缩文件，并按顺序自动尝试多种压缩格式，然后保存hash信息"""
    HASH_FILENAME = '.extracted_hash'
    
    try:
        archive_path = os.path.abspath(archive_path)
        base_dir = os.path.dirname(archive_path)
        file_name = os.path.splitext(os.path.basename(archive_path))[0].strip()
        extract_dir = os.path.join(base_dir, file_name)
        file_ext = os.path.splitext(archive_path)[1].lower()
        
        fail_mark = os.path.join(base_dir, f"{file_name}.extract_failed")
        if is_new_download and os.path.exists(fail_mark):
            print(f"[info] 跳过: {os.path.basename(archive_path)} | 之前多次下载解压均失败已标记")
            return False
        
        if not os.path.exists(extract_dir):
            os.makedirs(extract_dir)

        def _try_extract_zip(path, dest):
            with zipfile.ZipFile(path, 'r') as zip_ref:
                for member in zip_ref.infolist():
                    try:
                        zip_ref.extract(member, dest)
                    except zipfile.BadZipFile as e:
                        raise e 
                    except Exception:
                        new_name = member.filename.encode('cp437').decode('gbk', 'ignore')
                        if new_name != member.filename:
                            member.filename = new_name
                            zip_ref.extract(member, dest)

        def _try_extract_rar(path, dest):
            with rarfile.RarFile(path, 'r') as rar_ref:
                rar_ref.extractall(dest)

        def _try_extract_7z(path, dest):
            with py7zr.SevenZipFile(path, 'r') as sz_ref:
                sz_ref.extractall(dest)

        extractors = {
            'zip': (_try_extract_zip, zipfile.BadZipFile),
            'rar': (_try_extract_rar, (rarfile.NotRarFile, rarfile.BadRarFile)),
            '7z': (_try_extract_7z, py7zr.exceptions.Bad7zFile if hasattr(py7zr, 'exceptions') else Exception)
        }
        
        ext_key = file_ext.lstrip('.')
        attempt_order = [ext_key] + [k for k in extractors if k != ext_key] if ext_key in extractors else list(extractors.keys())

        extracted_successfully = False
        last_exception = None

        for format_key in attempt_order:
            extractor_func, format_exception_types = extractors[format_key]
            try:
                print(f"[extract] 正在尝试作为 {format_key.upper()} 格式解压: {os.path.basename(archive_path)}")
                extractor_func(archive_path, extract_dir)
                print(f"[extract] 成功作为 {format_key.upper()} 格式解压。")
                extracted_successfully = True
                break
            except format_exception_types as e:
                last_exception = e
                continue
            except (RuntimeError, rarfile.BadRarFile) as e:
                if "encrypted" in str(e).lower() or "password" in str(e).lower():
                    print(f"[extract] 跳过加密的压缩文件: {os.path.basename(archive_path)}")
                    if os.path.exists(extract_dir) and not os.listdir(extract_dir):
                        try: os.rmdir(extract_dir)
                        except: pass
                    return False
                last_exception = e
                continue
        
        if not extracted_successfully:
            raise last_exception or Exception("无法解压文件，已尝试所有支持的格式。")

        hash_file = os.path.join(base_dir, HASH_FILENAME)
        try:
            with open(hash_file, 'r', encoding='utf-8') as f:
                hash_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            hash_data = {}
        
        if not is_new_download:
             print(f"[Debug] 已记录解压哈希: {hash_value} -> {file_name}")

        hash_data[hash_value] = file_name
        with open(hash_file, 'w', encoding='utf-8') as f:
            json.dump(hash_data, f, indent=2, ensure_ascii=False)

        os.remove(archive_path)
        print(f"[extract] 成功解压到 {file_name} 并删除原文件: {os.path.basename(archive_path)}")

        if delete_extracted_types:
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    ext = os.path.splitext(file)[1].lower().lstrip('.')
                    if ext in delete_extracted_types:
                        try:
                            os.remove(os.path.join(root, file))
                            print(f"[extract] 删除指定类型文件: {file}")
                        except Exception as e:
                            print(f"[extract] 删除文件 {file} 失败: {str(e)}")
        return True

    except Exception as e:
        print(f"[Error] 解压失败 {os.path.basename(archive_path)}: {str(e)}")
        if not is_new_download:
            if os.path.exists(archive_path): os.remove(archive_path)
            print(f"[extract] 本地压缩文件解压失败，已删除: {os.path.basename(archive_path)}")
        else:
            retry_file = os.path.join(base_dir, f"{file_name}.retry_count")
            try: retry_count = int(open(retry_file, 'r').read().strip()) if os.path.exists(retry_file) else 0
            except: retry_count = 0
            
            retry_count += 1
            if retry_count >= 3:
                with open(fail_mark, 'w') as f: f.write(f"Failed at {datetime.datetime.now()}: {str(e)}")
                if os.path.exists(retry_file): os.remove(retry_file)
                print(f"[Error] 文件 {os.path.basename(archive_path)} 已尝试3次下载解压均失败，已标记为永久跳过")
            else:
                with open(retry_file, 'w') as f: f.write(str(retry_count))
                print(f"[warning] 文件 {os.path.basename(archive_path)} 下载解压失败第{retry_count}次，将重试下载")
                if "encrypted" not in str(e).lower() and "password" not in str(e).lower():
                    if os.path.exists(archive_path): os.remove(archive_path)
        
        if 'extract_dir' in locals() and os.path.exists(extract_dir):
            try:
                if not os.listdir(extract_dir): os.rmdir(extract_dir)
            except: pass
        return False

def process_existing_archives(directory: str, delete_extracted_types: list = []):
    """处理目录中已存在的压缩文件"""
    print("[NAS] 正在检查并处理已存在的压缩文件...")
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.zip', '.7z', '.rar')):
                archive_path = os.path.join(root, file)
                hash_value = get_sha256_hash(archive_path)
                extract_archive(archive_path, hash_value, delete_extracted_types, is_new_download=False)

def clear_failed_marks(directory: str):
    """清除所有永久跳过标记"""
    print("[NAS] 清除永久跳过标记...")
    count = 0
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.extract_failed'):
                try:
                    os.remove(os.path.join(root, file))
                    count += 1
                except: pass
    if count > 0:
        print(f"[NAS] 已清除 {count} 个永久跳过标记")