# API 回歸測試工具

## 使用方式

1. 啟動要測試的 Spring Boot 專案，並確認 `/v3/api-docs` 可存取。
2. 執行 `API回歸測試工具.exe`，瀏覽器會自動開啟 `http://127.0.0.1:8765`。
3. 到「系統設定」填寫 DB2、專案、Domain、Token Header 與回應 Return Code Path。
4. 儲存後回到「測試執行」，選擇專案與環境，輸入本次 Token。
5. 讀取 API 清單，執行全部或勾選項目。

工具從 `DB2WMS.ADM_API_ACCESS_LOG` 只選 `RETURN_CODE=200` 的歷史紀錄，依時間由新到舊為每個 API 取一筆。實際回歸結果以 HTTP Status 200 且 Response 業務 Return Code 為 `0000` 判定 PASS。

## 設定與報告

- `config.yaml`：第一次啟動時在 EXE 同目錄自動建立，可隨時從 UI 修改。
- `reports/`：測試結果 JSON 報告。
- Token 只在當次執行的記憶體使用，不寫入 Config 或報告。

## DB2 驅動

EXE 已包含 IBM CLI Driver。若「測試 DB2 連線」顯示 DLL 載入錯誤，可在設定中改用公司既有的 IBM Data Server Driver 安裝路徑重新建置，或確認公司電腦的 DB2 Client 與 Windows 架構皆為 x64。

## 重新建置

安裝 Python 後：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\build.ps1
```
