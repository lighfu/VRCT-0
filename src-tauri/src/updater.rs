//! アプリ内の更新 (Velopack)。
//!
//! 画面からは Tauri のコマンドで呼び、状態が変わるたびに `app-update://state`
//! イベントで画面へ送る。確認とダウンロードはネットワークを使うので、
//! コマンドは待たずに返し、別のスレッドで動かす。
//! 落とし終えた更新は、アプリを閉じるとき (RunEvent::Exit) に Velopack の
//! Update.exe へ渡して入れ替える。「今すぐ再起動」を選んだときだけ再起動する。
//! 閉じる前に落ちた場合は、次の起動時に VelopackApp が入れ替える (Velopack の既定)。

use std::sync::{Arc, Mutex};

use serde::Serialize;

pub const STATE_EVENT: &str = "app-update://state";
pub const REPO_URL: &str = "https://github.com/lighfu/VRCT-0";
/// 実機確認用: 設定すると、このフォルダ (vpk pack の出力) を更新元にする。
pub const FEED_DIR_ENV: &str = "VRCT_UPDATE_FEED_DIR";

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailedStage {
    Check,
    Download,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum UpdateState {
    /// Velopack で入れていない (開発中など)。
    NotInstalled,
    Idle,
    Checking,
    UpToDate,
    Available { version: String, size_bytes: u64, is_downgrade: bool },
    Downloading { version: String, percent: u8 },
    Ready { version: String, restart_requested: bool },
    Failed { stage: FailedStage, message: String, version: Option<String> },
}

#[derive(Clone, Debug, PartialEq)]
pub struct UpdateQuery {
    pub prerelease: bool,
    pub allow_downgrade: bool,
}

/// チャンネルと今の版から、何を更新の対象にするかを決める。
/// ベータ版を使っている人が安定版に切り替えたときだけ、版が下がることを許す。
pub fn update_query(channel: &str, current_version: &str) -> UpdateQuery {
    let beta = channel == "beta";
    let current_is_prerelease = current_version.contains('-');
    UpdateQuery { prerelease: beta, allow_downgrade: !beta && current_is_prerelease }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Offer {
    pub version: String,
    pub size_bytes: u64,
    pub is_downgrade: bool,
}

pub type Progress = Arc<dyn Fn(u8) + Send + Sync>;

/// Velopack との間。テストでは偽物に差し替える。
pub trait UpdateBackend: Send + Sync {
    fn is_installed(&self) -> bool;
    fn current_version(&self) -> String;
    fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String>;
    /// 直前の check が返した版を落とす。戻る前に進み具合の通知を出し終える。
    fn download(&self, progress: Progress) -> Result<(), String>;
    /// 落とした版を、このプロセスが終わったあとに入れ替えてもらう。
    fn apply_after_exit(&self, restart: bool) -> Result<(), String>;
}

type Emitter = Arc<dyn Fn(&UpdateState) + Send + Sync>;

struct Inner {
    backend: Arc<dyn UpdateBackend>,
    state: Mutex<UpdateState>,
    emitter: Mutex<Option<Emitter>>,
}

#[derive(Clone)]
pub struct Updater {
    inner: Arc<Inner>,
}

impl Updater {
    pub fn new(backend: Arc<dyn UpdateBackend>) -> Self {
        let initial = if backend.is_installed() { UpdateState::Idle } else { UpdateState::NotInstalled };
        Updater {
            inner: Arc::new(Inner { backend, state: Mutex::new(initial), emitter: Mutex::new(None) }),
        }
    }

    pub fn set_emitter(&self, emitter: Emitter) {
        *self.inner.emitter.lock().unwrap() = Some(emitter);
    }

    pub fn state(&self) -> UpdateState {
        self.inner.state.lock().unwrap().clone()
    }

    /// 今の状態から次の状態を決めて置き換え、画面へ送る。None なら何もしない。
    fn transition(&self, decide: impl FnOnce(&UpdateState) -> Option<UpdateState>) -> Option<UpdateState> {
        let next = {
            let mut state = self.inner.state.lock().unwrap();
            let next = decide(&state)?;
            *state = next.clone();
            next
        };
        let emitter = self.inner.emitter.lock().unwrap().clone();
        if let Some(emit) = emitter {
            emit(&next);
        }
        Some(next)
    }

    /// 新しい版を確かめる。manual は「更新を確認」を押したとき (失敗を表示する)。
    pub fn check(&self, channel: &str, manual: bool) {
        let started = self.transition(|state| match state {
            UpdateState::NotInstalled
            | UpdateState::Checking
            | UpdateState::Downloading { .. }
            | UpdateState::Ready { .. } => None,
            _ => Some(UpdateState::Checking),
        });
        if started.is_none() {
            return;
        }
        let query = update_query(channel, &self.inner.backend.current_version());
        let next = match self.inner.backend.check(&query) {
            Ok(Some(offer)) => UpdateState::Available {
                version: offer.version,
                size_bytes: offer.size_bytes,
                is_downgrade: offer.is_downgrade,
            },
            Ok(None) => UpdateState::UpToDate,
            Err(message) if manual => UpdateState::Failed { stage: FailedStage::Check, message, version: None },
            Err(message) => {
                crate::startup_log(&format!("Update check failed: {message}"));
                UpdateState::Idle
            }
        };
        self.transition(|_| Some(next));
    }

    /// 見つかった版を落とす。失敗したあとの再試行もここから。
    pub fn download(&self) {
        let started = self.transition(|state| match state {
            UpdateState::Available { version, .. }
            | UpdateState::Failed { stage: FailedStage::Download, version: Some(version), .. } => {
                Some(UpdateState::Downloading { version: version.clone(), percent: 0 })
            }
            _ => None,
        });
        let version = match started {
            Some(UpdateState::Downloading { version, .. }) => version,
            _ => return,
        };
        let me = self.clone();
        let progress: Progress = Arc::new(move |percent| {
            me.transition(|state| match state {
                UpdateState::Downloading { version, percent: current } if *current != percent => {
                    Some(UpdateState::Downloading { version: version.clone(), percent })
                }
                _ => None,
            });
        });
        let next = match self.inner.backend.download(progress) {
            Ok(()) => UpdateState::Ready { version, restart_requested: false },
            Err(message) => UpdateState::Failed { stage: FailedStage::Download, message, version: Some(version) },
        };
        self.transition(|_| Some(next));
    }

    /// 「今すぐ再起動して更新」。準備ができているときだけ受け付ける。
    pub fn request_restart(&self) -> bool {
        self.transition(|state| match state {
            UpdateState::Ready { version, .. } => {
                Some(UpdateState::Ready { version: version.clone(), restart_requested: true })
            }
            _ => None,
        })
        .is_some()
    }

    /// アプリを閉じるときに呼ぶ。落とし終えた版があれば入れ替えを頼む。
    pub fn apply_on_exit(&self) {
        if let UpdateState::Ready { restart_requested, .. } = self.state() {
            if let Err(message) = self.inner.backend.apply_after_exit(restart_requested) {
                crate::startup_log(&format!("Applying the update failed: {message}"));
            }
        }
    }
}

/// Velopack の UpdateManager を使う本物。
pub struct VelopackBackend {
    repo_url: String,
    pending: Mutex<Option<(velopack::UpdateManager, velopack::UpdateInfo)>>,
}

impl VelopackBackend {
    pub fn new(repo_url: &str) -> Self {
        VelopackBackend { repo_url: repo_url.to_string(), pending: Mutex::new(None) }
    }

    fn manager(&self, query: &UpdateQuery) -> Result<velopack::UpdateManager, String> {
        let options = velopack::UpdateOptions {
            AllowVersionDowngrade: query.allow_downgrade,
            ..Default::default()
        };
        let source: Box<dyn velopack::sources::UpdateSource> = match std::env::var_os(FEED_DIR_ENV) {
            Some(dir) if !dir.is_empty() => Box::new(velopack::sources::FileSource::new(dir)),
            _ => Box::new(velopack::sources::GithubSource::new(&self.repo_url, None, query.prerelease)),
        };
        velopack::UpdateManager::new_boxed(source, Some(options), None).map_err(|e| e.to_string())
    }

    fn local_manager() -> Option<velopack::UpdateManager> {
        velopack::UpdateManager::new(velopack::sources::NoneSource {}, None, None).ok()
    }
}

/// 落とす大きさ。差分があれば差分の合計、無ければ丸ごとのパッケージ。
pub fn offer_from(info: &velopack::UpdateInfo) -> Offer {
    let size_bytes = if info.BaseRelease.is_some() && !info.DeltasToTarget.is_empty() {
        info.DeltasToTarget.iter().map(|delta| delta.Size).sum()
    } else {
        info.TargetFullRelease.Size
    };
    Offer {
        version: info.TargetFullRelease.Version.clone(),
        size_bytes,
        is_downgrade: info.IsDowngrade,
    }
}

impl UpdateBackend for VelopackBackend {
    fn is_installed(&self) -> bool {
        Self::local_manager().is_some()
    }

    fn current_version(&self) -> String {
        Self::local_manager().map(|m| m.get_current_version_as_string()).unwrap_or_default()
    }

    fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String> {
        let manager = self.manager(query)?;
        match manager.check_for_updates().map_err(|e| e.to_string())? {
            velopack::UpdateCheck::UpdateAvailable(info) => {
                let offer = offer_from(&info);
                *self.pending.lock().unwrap() = Some((manager, *info));
                Ok(Some(offer))
            }
            _ => Ok(None),
        }
    }

    fn download(&self, progress: Progress) -> Result<(), String> {
        let (manager, info) = self.pending.lock().unwrap().clone().ok_or("no update to download")?;
        let (sender, receiver) = std::sync::mpsc::channel::<i16>();
        let relay = std::thread::spawn(move || {
            for percent in receiver {
                progress(percent.clamp(0, 100) as u8);
            }
        });
        let result = manager.download_updates(&info, Some(sender)).map_err(|e| e.to_string());
        let _ = relay.join();
        result
    }

    fn apply_after_exit(&self, restart: bool) -> Result<(), String> {
        let (manager, info) = self.pending.lock().unwrap().clone().ok_or("no downloaded update")?;
        manager
            .wait_exit_then_apply_updates(&info, !restart, restart, Vec::<String>::new())
            .map_err(|e| e.to_string())
    }
}

#[tauri::command]
pub fn updater_state(updater: tauri::State<'_, Updater>) -> UpdateState {
    updater.state()
}

#[tauri::command]
pub fn updater_check(updater: tauri::State<'_, Updater>, channel: String, manual: bool) {
    let updater = updater.inner().clone();
    std::thread::spawn(move || updater.check(&channel, manual));
}

#[tauri::command]
pub fn updater_download(updater: tauri::State<'_, Updater>) {
    let updater = updater.inner().clone();
    std::thread::spawn(move || updater.download());
}

#[tauri::command]
pub fn updater_restart_now(updater: tauri::State<'_, Updater>) -> bool {
    updater.request_restart()
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FakeBackend {
        installed: bool,
        version: String,
        check_result: Mutex<Result<Option<Offer>, String>>,
        download_result: Mutex<Result<(), String>>,
        progress_steps: Vec<u8>,
        last_query: Mutex<Option<UpdateQuery>>,
        download_calls: Mutex<u32>,
        applied: Mutex<Vec<bool>>,
    }

    impl FakeBackend {
        fn new() -> Self {
            FakeBackend {
                installed: true,
                version: "3.5.1".into(),
                check_result: Mutex::new(Ok(Some(offer("3.6.0")))),
                download_result: Mutex::new(Ok(())),
                progress_steps: vec![10, 10, 50, 100],
                last_query: Mutex::new(None),
                download_calls: Mutex::new(0),
                applied: Mutex::new(Vec::new()),
            }
        }
    }

    fn offer(version: &str) -> Offer {
        Offer { version: version.into(), size_bytes: 1234, is_downgrade: false }
    }

    impl UpdateBackend for FakeBackend {
        fn is_installed(&self) -> bool {
            self.installed
        }
        fn current_version(&self) -> String {
            self.version.clone()
        }
        fn check(&self, query: &UpdateQuery) -> Result<Option<Offer>, String> {
            *self.last_query.lock().unwrap() = Some(query.clone());
            self.check_result.lock().unwrap().clone()
        }
        fn download(&self, progress: Progress) -> Result<(), String> {
            *self.download_calls.lock().unwrap() += 1;
            for step in &self.progress_steps {
                progress(*step);
            }
            self.download_result.lock().unwrap().clone()
        }
        fn apply_after_exit(&self, restart: bool) -> Result<(), String> {
            self.applied.lock().unwrap().push(restart);
            Ok(())
        }
    }

    fn updater_with(backend: FakeBackend) -> (Updater, Arc<FakeBackend>, Arc<Mutex<Vec<UpdateState>>>) {
        let backend = Arc::new(backend);
        let updater = Updater::new(backend.clone());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let sink = seen.clone();
        updater.set_emitter(Arc::new(move |state| sink.lock().unwrap().push(state.clone())));
        (updater, backend, seen)
    }

    #[test]
    fn update_query_rules() {
        assert_eq!(update_query("beta", "3.5.1"), UpdateQuery { prerelease: true, allow_downgrade: false });
        assert_eq!(update_query("beta", "3.6.0-beta.1"), UpdateQuery { prerelease: true, allow_downgrade: false });
        assert_eq!(update_query("stable", "3.6.0-beta.1"), UpdateQuery { prerelease: false, allow_downgrade: true });
        assert_eq!(update_query("stable", "3.5.1"), UpdateQuery { prerelease: false, allow_downgrade: false });
    }

    #[test]
    fn not_installed_never_checks() {
        let mut fake = FakeBackend::new();
        fake.installed = false;
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", true);
        assert_eq!(updater.state(), UpdateState::NotInstalled);
        assert!(backend.last_query.lock().unwrap().is_none());
    }

    #[test]
    fn check_finds_an_update() {
        let (updater, backend, seen) = updater_with(FakeBackend::new());
        updater.check("beta", false);
        assert_eq!(
            updater.state(),
            UpdateState::Available { version: "3.6.0".into(), size_bytes: 1234, is_downgrade: false }
        );
        assert_eq!(backend.last_query.lock().unwrap().clone().unwrap().prerelease, true);
        assert_eq!(seen.lock().unwrap()[0], UpdateState::Checking);
    }

    #[test]
    fn check_reports_up_to_date() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Ok(None);
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::UpToDate);
    }

    #[test]
    fn automatic_check_failure_is_silent() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Err("403 rate limit".into());
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::Idle);
    }

    #[test]
    fn manual_check_failure_is_shown() {
        let fake = FakeBackend::new();
        *fake.check_result.lock().unwrap() = Err("offline".into());
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", true);
        assert_eq!(
            updater.state(),
            UpdateState::Failed { stage: FailedStage::Check, message: "offline".into(), version: None }
        );
    }

    #[test]
    fn download_reports_progress_then_ready() {
        let (updater, _, seen) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        let percents: Vec<u8> = seen
            .lock()
            .unwrap()
            .iter()
            .filter_map(|s| match s {
                UpdateState::Downloading { percent, .. } => Some(*percent),
                _ => None,
            })
            .collect();
        // 同じ値 (10) は 2 回送らない。
        assert_eq!(percents, vec![0, 10, 50, 100]);
    }

    #[test]
    fn download_failure_can_be_retried() {
        let fake = FakeBackend::new();
        *fake.download_result.lock().unwrap() = Err("connection reset".into());
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", false);
        updater.download();
        assert_eq!(
            updater.state(),
            UpdateState::Failed {
                stage: FailedStage::Download,
                message: "connection reset".into(),
                version: Some("3.6.0".into())
            }
        );
        *backend.download_result.lock().unwrap() = Ok(());
        updater.download();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        assert_eq!(*backend.download_calls.lock().unwrap(), 2);
    }

    #[test]
    fn check_is_ignored_while_downloading_or_ready() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        *backend.last_query.lock().unwrap() = None;
        updater.check("beta", true);
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        assert!(backend.last_query.lock().unwrap().is_none());
    }

    #[test]
    fn second_download_is_ignored() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.download();
        updater.download();
        assert_eq!(*backend.download_calls.lock().unwrap(), 1);
    }

    #[test]
    fn apply_on_exit_only_when_ready() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.apply_on_exit();
        assert!(backend.applied.lock().unwrap().is_empty());
        updater.download();
        updater.apply_on_exit();
        assert_eq!(*backend.applied.lock().unwrap(), vec![false]);
    }

    #[test]
    fn restart_now_is_passed_to_apply() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        assert!(!updater.request_restart());
        updater.check("stable", false);
        updater.download();
        assert!(updater.request_restart());
        updater.apply_on_exit();
        assert_eq!(*backend.applied.lock().unwrap(), vec![true]);
    }

    #[test]
    fn state_json_shape() {
        let json = serde_json::to_value(UpdateState::Downloading { version: "3.6.0".into(), percent: 5 }).unwrap();
        assert_eq!(json, serde_json::json!({"status": "downloading", "version": "3.6.0", "percent": 5}));
        let json = serde_json::to_value(UpdateState::Failed {
            stage: FailedStage::Check,
            message: "x".into(),
            version: None,
        })
        .unwrap();
        assert_eq!(json, serde_json::json!({"status": "failed", "stage": "check", "message": "x", "version": null}));
    }

    #[test]
    fn offer_size_prefers_deltas() {
        let mut info = velopack::UpdateInfo::default();
        info.TargetFullRelease.Version = "3.6.0".into();
        info.TargetFullRelease.Size = 400;
        assert_eq!(offer_from(&info).size_bytes, 400);
        info.BaseRelease = Some(velopack::VelopackAsset::default());
        let mut delta = velopack::VelopackAsset::default();
        delta.Size = 30;
        info.DeltasToTarget = vec![delta.clone(), delta];
        assert_eq!(offer_from(&info).size_bytes, 60);
    }
}
