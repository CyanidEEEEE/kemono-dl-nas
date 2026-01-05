# Role: Python Developer / Code Maintainer
# Task: Merge upstream updates into a customized NAS fork of kemono-dl

## 项目背景
这是一个基于 `alphaslayer1964/kemono-dl` (Refactored Package Version) 的深度定制版本。
这个版本运行在 NAS 上，有两个核心目标：
1. **NAS 自动化增强**：下载后自动解压 (zip/rar/7z)，支持查重（基于哈希跳过已解压文件），支持删除源文件。
2. **旧版路径完美兼容 (Legacy Compatibility)**：必须保证下载的文件名和路径与旧版单文件脚本 (`kemono-dl.py`) 完全一致，防止重复下载 TB 级的数据。

## 关键文件与修改点 (Critical Changes)
在合并原作者的新代码时，请务必保留以下文件的特定逻辑，**不要盲目覆盖**：

### 1. `kemono_dl/kemono_dl.py` (高危文件)
这是改动最大的文件，包含路径兼容逻辑。
* **保留常量**：`DEFAULT_OUTPUT_TEMPLATE` 必须是旧版格式（不带 :%Y%m%d）。
* **保留方法 `get_processed_variables(self, template_variables)`**：
    * 这个函数负责将索引从 0-based 改为 1-based (index + 1)。
    * 负责将索引自动补零 (zfill)。
    * 负责将无效日期转换为字符串 "None" 而不是 "00010101"。
    * **如果丢了这个函数，所有文件名都会变，导致全量重复下载！**
* **保留 Hook**：在 `download_post_attachments` 方法中，下载前有“查重逻辑”，下载后有“解压逻辑 (`extract_archive`)”。

### 2. `kemono_dl/utils.py` (工具库)
* **保留 `generate_file_path` 中的 `_sanitize` 逻辑**：
    * 必须使用复刻旧版的清理逻辑（替换非法字符为下划线，保留末尾的点并替换为下划线，而不是删除）。
* **保留底部 NAS 函数**：文件末尾添加的 `extract_archive` (含 rar/7z 支持), `process_existing_archives`, `clear_failed_marks` 等函数必须保留。

### 3. `kemono_dl/__main__.py` (入口)
* **保留参数**：`--delete-extracted-types`, `--no-auto-extract`, `--clear-failed-marks`。

### 4. `requirements.txt`
* **保留依赖**：`rarfile`, `py7zr`。

## 更新/合并策略 (Update Strategy)
请按以下步骤帮我更新代码：
1.  **安全覆盖**：对于 `models.py`, `downloader.py`, `session.py`, `version.py` 等未提及的文件，直接使用原作者的最新版覆盖，以获取性能提升和 Bug 修复。
2.  **手动合并**：
    * 读取原作者最新的 `kemono_dl/kemono_dl.py`，将我的 `get_processed_variables` 方法插回去，并在 `download_post_attachments` 里的适当位置重新插入解压代码。
    * 读取原作者最新的 `utils.py`，确保 `generate_file_path` 依然兼容旧版命名，并将我的解压函数追加到文件末尾。

## 验证清单 (Verification)
更新完成后，请检查：
1.  文件名索引是否是从 1 开始的？（例如 `1_File.zip` 而不是 `0_File.zip`）
2.  日期解析失败是否显示为 `[None]`？
3.  自动解压功能是否还在？
