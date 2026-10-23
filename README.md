# API 回歸測試工具

Windows 本機 API 測試工具。API 清單與測試案例保存在 SQLite，可逐筆調整 Request，再單筆、多筆或全部執行，並產生 HTML 與 JSON 報告。

## 主要功能

- 從執行中的 Spring Boot `/v3/api-docs` 匯入 API。
- 從 Repo 內的 OpenAPI 文件或 Spring Controller Annotation 匯入 API。
- 將 API 與測試 Request 保存到 `test-data.db`。
- Request 可個別調整 Path、Query、Header、Body 與預期結果。
- 支援全域、專案、環境、本次執行及案例 Header。
- 可從 DB2 或 Oracle `ADM_API_ACCESS_LOGS` 依 `API_NAME` 產生 Request。
- 支援單筆、勾選多筆及全部啟用案例執行。
- 產生可離線開啟的 HTML 報告及原始 JSON 報告。

## 使用方式

1. 執行 `API回歸測試工具.exe`，瀏覽器會自動開啟本機操作畫面。
2. 到「系統設定」建立專案、環境、共用 Header 與資料庫欄位對應。
3. 到「測試執行」選擇已保存案例、OpenAPI URL 或 Spring Boot Repo。
4. 編輯並保存每一筆測試案例。
5. 選擇目標環境，單筆或勾選多筆執行。
6. 從「報告歷史」開啟 HTML／JSON 報告。

關閉或離開操作頁面時，瀏覽器會先顯示離開確認；確認後 EXE 會停止本機服務並釋放 Port。重新整理頁面會在短暫緩衝內重新連線，不會誤關服務。也可以使用左側的「關閉工具」按鈕主動停止。

Token 只在本次執行使用；Authorization、Cookie、API Key 等敏感 Header 會在報告中遮罩。

## 檔案

- `config.yaml`：應用程式、專案、環境、Header 及資料庫設定。
- `test-data.db`：API、測試案例及報告索引。
- `reports/`：HTML 與 JSON 測試報告。

## 開發環境

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\build.ps1
```

DB2 使用內含的 IBM CLI Driver。Oracle 預設使用 `python-oracledb` Thin Mode，不要求另外安裝 Oracle Client。
