# Windows 建置版本

下載 `API回歸測試工具.exe` 後直接執行，瀏覽器會自動開啟本機操作畫面。

在「系統設定 → Repo 與環境」按「新增 Repo」，Windows 檔案總管會讓你選擇要測試的 Spring Boot Repo。每個 Repo 就是一個測試單位，不存在額外的專案層級。

「刪除 Repo」會清除工具內保存的 API、測試案例與報告，不會刪除硬碟上的 Repo 原始碼資料夾。

首次執行時，程式會在執行檔旁建立或使用以下本機資料：

- `config.yaml`：應用程式、專案、環境與資料庫設定。
- `test-data.db`：API、測試案例及報告索引。
- `reports/`：HTML 與 JSON 測試報告。

Repository 僅提供安全的 `config.example.yaml` 範例，不包含開發環境的設定與測試資料。
