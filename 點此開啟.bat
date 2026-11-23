@echo off
chcp 65001 >nul
title 台灣上市公司 ESG 溫室氣體統計系統
cd /d "%~dp0"
echo ===================================================
echo       正在檢查並自動安裝所需的 Python 套件...
echo ===================================================
py -m pip install -r requirement.txt

echo.
echo ===================================================
echo       正在啟動 ESG 統計系統，即將開啟瀏覽器...
echo ===================================================


py -m streamlit run debug.py --client.showErrorDetails=fasle --server.port 8502


pause