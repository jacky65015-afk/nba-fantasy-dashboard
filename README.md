# 2026–27 NBA Fantasy 六隊作戰中心

靜態網站：`index.html`。NBA 公開資料由 `update_data.py` 取得並寫入 `data/nba.json`。六支聯盟的私人球員名單及備註保留在各自瀏覽器的 `localStorage`，**不會自動從 Yahoo 擷取**。請勿輸入 Yahoo 密碼或 Cookie。

## 本機試用

```bash
python update_data.py
python -m http.server 8000
```

開啟 `http://localhost:8000`。也可以直接打開 `index.html`，但某些瀏覽器會阻止 `file://` 載入 `data/nba.json`，所以建議用本機伺服器或部署。

## GitHub Pages 上線及每四小時更新

1. 新增一個 GitHub 公開 repository，上傳此資料夾的**全部內容**（包括 `.github/workflows/update.yml`）。
2. GitHub repository → Settings → Pages → Build and deployment → Source 選 **Deploy from a branch**，Branch 選 `main`、資料夾選 `/ (root)`。
3. Actions → **NBA data every four hours** → **Run workflow**，先手動跑一次。需讓 Actions 有寫入 repository 的權限（Settings → Actions → General → Workflow permissions → Read and write permissions）。若此選項受組織政策限制，需改為部署產物工作流程或設定適當權限。
4. 之後排程會每 **4 小時**（UTC 00:13、04:13、08:13、12:13、16:13、20:13；台灣時間 08:13、12:13、16:13、20:13、隔日00:13、04:13）觸發。GitHub 排程不是保證準時，可能延遲；閒置公開 repository 的 schedule 可能被停用，必須重新啟用。
5. 網站開啟時立即讀資料，並每四小時檢查資料檔；按「檢查更新」僅重新讀取網站資料檔，**不會直接執行 GitHub 排程**。

## 資料規則與限制

- 公開 ESPN 網站端點屬**非官方、未保證長期可用**的 API；資料更新時若來源失敗，會保留舊資料與警告、並記錄最後成功時間。使用前需自行確認使用條款與營運需求。
- 官方 NBA 日期和比分依美東賽程日，與台灣日期可能不同；頁面按 **美東賽程日期** 統一顯示。
- 傷病只列出能由來源**明確解析的項目**；空白不代表健康。來源可能涵蓋不完整，不能取代官方傷病報告。
- 每日球員數據限本次更新擷取的最近 7 天已取得 box score 的比賽。頁面顯示來源資料時間及錯誤，**不會憑空補數據**。
- 無連線/更新失敗時，可能使用上次成功的快取；網站會顯示快取時間。
- Yahoo Fantasy 名單和 FA 是私有聯盟資料，需自行輸入或另行以合規 Yahoo OAuth 授權整合；此版本**不會**同步你的 Yahoo 名單。
