$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "找不到 .venv。請先安裝 Python 並執行：python -m venv .venv"
}
& $Python -m PyInstaller --noconfirm --clean --onefile --name "API回歸測試工具" `
    --add-data "static;static" `
    --add-data "config.example.yaml;." `
    --add-data ".venv\Lib\site-packages\clidriver;clidriver" `
    --add-binary ".venv\Lib\site-packages\ibm_db.cp312-win_amd64.pyd;." `
    --hidden-import uvicorn.logging `
    --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto `
    --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    run.py
Copy-Item "config.example.yaml" "dist\config.example.yaml" -Force
Copy-Item "README.md" "dist\README.md" -Force
Write-Host "完成：dist\API回歸測試工具.exe"
