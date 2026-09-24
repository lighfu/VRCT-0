//! アプリ内の更新 (Velopack)。
//!
//! 画面からは Tauri のコマンドで呼び、状態が変わるたびに `app-update://state`
//! イベントで画面へ送る。確認とダウンロードはネットワークを使うので、
//! コマンドは待たずに返し、別のスレッドで動かす。
//! 落とし終えた更新は、アプリを閉じるとき (RunEvent::Exit) に Velopack の
//! Update.exe へ渡して入れ替える。「今すぐ再起動」を選んだときだけ再起動する。
//! 閉じる前に落ちた場合は、次の起動時に VelopackApp が入れ替える (Velopack の既定)。
//! ただしこれは版が上がる更新だけで、版を下げる更新 (ベータ版→安定版) は VelopackApp が
//! 落としたパッケージを消すので入れ替わらず、次の確認でまた通知する。
//!
//! Velopack の GitHub のソースには通信の時間切れが無い。スリープからの復帰やネットワークの
//! 切り替えで通信が止まると、確認がいつまでも終わらない。確認は CHECK_TIMEOUT で諦める。
//! ダウンロードの止まりはまだ扱っていない (Velopack のロックを持ったまま止まるので、
//! やり直すにはアプリの再起動が要る。最初の公開リリースの前に手当てする)。

use std::any::Any;
use std::collections::HashMap;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use serde::Serialize;

pub const STATE_EVENT: &str = "app-update://state";
pub const REPO_URL: &str = "https://github.com/lighfu/VRCT-0";
/// 実機確認用: 設定すると、このフォルダ (vpk pack の出力) を更新元にする。
pub const FEED_DIR_ENV: &str = "VRCT_UPDATE_FEED_DIR";
/// 新しい版の確認を待つ長さ。これを過ぎたら諦める (起動時なら何も出さず、手で押したなら失敗と出す)。
pub const CHECK_TIMEOUT: Duration = Duration::from_secs(30);
/// 確認が時間切れになったときの Failed の message。
pub const CHECK_TIMEOUT_MESSAGE: &str = "timeout";

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
    /// check が返した版 `version` を落とす。戻る前に進み具合の通知を出し終える。
    fn download(&self, version: &str, progress: Progress) -> Result<(), String>;
    /// 落とした版 `version` を、このプロセスが終わったあとに入れ替えてもらう。
    fn apply_after_exit(&self, version: &str, restart: bool) -> Result<(), String>;
}

type Emitter = Arc<dyn Fn(&UpdateState) + Send + Sync>;

struct Inner {
    backend: Arc<dyn UpdateBackend>,
    state: Mutex<UpdateState>,
    emitter: Mutex<Option<Emitter>>,
    check_timeout: Duration,
    /// 真なら、このプロセスの終わりに apply_on_exit が何もしない (画面からの普通の再起動)。
    skip_apply_on_exit: AtomicBool,
}

/// 別のスレッドが持ったまま落ちても (毒が回っても) 使い続ける。更新の状態が固まらないように。
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn panic_message(what: &str, payload: Box<dyn Any + Send>) -> String {
    let detail = payload
        .downcast_ref::<&str>()
        .map(|text| text.to_string())
        .or_else(|| payload.downcast_ref::<String>().cloned())
        .unwrap_or_else(|| "unknown panic".to_string());
    format!("{what} stopped unexpectedly: {detail}")
}

#[derive(Clone)]
pub struct Updater {
    inner: Arc<Inner>,
}

impl Updater {
    pub fn new(backend: Arc<dyn UpdateBackend>) -> Self {
        Self::with_check_timeout(backend, CHECK_TIMEOUT)
    }

    pub fn with_check_timeout(backend: Arc<dyn UpdateBackend>, check_timeout: Duration) -> Self {
        let initial = if backend.is_installed() { UpdateState::Idle } else { UpdateState::NotInstalled };
        Updater {
            inner: Arc::new(Inner {
                backend,
                state: Mutex::new(initial),
                emitter: Mutex::new(None),
                check_timeout,
                skip_apply_on_exit: AtomicBool::new(false),
            }),
        }
    }

    pub fn set_emitter(&self, emitter: Emitter) {
        *lock(&self.inner.emitter) = Some(emitter);
    }

    pub fn state(&self) -> UpdateState {
        lock(&self.inner.state).clone()
    }

    /// 今の状態から次の状態を決めて置き換え、画面へ送る。None なら何もしない。
    fn transition(&self, decide: impl FnOnce(&UpdateState) -> Option<UpdateState>) -> Option<UpdateState> {
        let next = {
            let mut state = lock(&self.inner.state);
            let next = decide(&state)?;
            *state = next.clone();
            next
        };
        let emitter = lock(&self.inner.emitter).clone();
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
        let next = match self.check_with_timeout(query) {
            Ok(Some(offer)) => UpdateState::Available {
                version: offer.version,
                size_bytes: offer.size_bytes,
                is_downgrade: offer.is_downgrade,
            },
            Ok(None) => UpdateState::UpToDate,
            Err(message) => {
                let kind = if manual { "manual" } else { "automatic" };
                crate::startup_log(&format!("Update check failed ({kind}): {message}"));
                if manual {
                    UpdateState::Failed { stage: FailedStage::Check, message, version: None }
                } else {
                    UpdateState::Idle
                }
            }
        };
        self.transition(|_| Some(next));
    }

    /// 確認を別のスレッドで動かし、check_timeout を過ぎたら諦める。止まったスレッドはそのまま
    /// 残るが、あとで結果が出ても受け取り口がもう無いので捨てられる (状態は変えない)。
    fn check_with_timeout(&self, query: UpdateQuery) -> Result<Option<Offer>, String> {
        let backend = self.inner.backend.clone();
        let (sender, receiver) = mpsc::channel();
        let worker = std::thread::Builder::new().name("update-check".into()).spawn(move || {
            let result = catch_unwind(AssertUnwindSafe(|| backend.check(&query)))
                .unwrap_or_else(|payload| Err(panic_message("the update check", payload)));
            let _ = sender.send(result);
        });
        if let Err(error) = worker {
            return Err(format!("could not start the update check: {error}"));
        }
        match receiver.recv_timeout(self.inner.check_timeout) {
            Ok(result) => result,
            Err(RecvTimeoutError::Timeout) => Err(CHECK_TIMEOUT_MESSAGE.to_string()),
            Err(RecvTimeoutError::Disconnected) => Err("the update check stopped unexpectedly".to_string()),
        }
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
        let result = catch_unwind(AssertUnwindSafe(|| self.inner.backend.download(&version, progress)))
            .unwrap_or_else(|payload| Err(panic_message("the update download", payload)));
        let next = match result {
            Ok(()) => UpdateState::Ready { version, restart_requested: false },
            Err(message) => {
                crate::startup_log(&format!("Update download of {version} failed: {message}"));
                UpdateState::Failed { stage: FailedStage::Download, message, version: Some(version) }
            }
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

    /// このプロセスの終わりでは入れ替えを頼まないようにする。更新役を通さずに起動し直すとき
    /// (restart::app_restart の普通の道) に使う。確かめたあとで準備ができた版をここで入れ替えると、
    /// 起動し直した新しいアプリと Velopack の入れ替えがぶつかるため。その版は次の起動の確認でまた見つかる。
    pub fn skip_apply_on_exit(&self) {
        self.inner.skip_apply_on_exit.store(true, Ordering::SeqCst);
    }

    /// アプリを閉じるときに呼ぶ。落とし終えた版があれば入れ替えを頼む。
    pub fn apply_on_exit(&self) {
        if self.inner.skip_apply_on_exit.load(Ordering::SeqCst) {
            return;
        }
        if let UpdateState::Ready { version, restart_requested } = self.state() {
            if let Err(message) = self.inner.backend.apply_after_exit(&version, restart_requested) {
                crate::startup_log(&format!("Applying the update failed: {message}"));
            }
        }
    }
}

/// Velopack の UpdateManager を使う本物。
pub struct VelopackBackend {
    repo_url: String,
    /// check が見つけた版ごとの UpdateManager と UpdateInfo。時間切れのあとに遅れて終わった確認が
    /// 別の版を置いても、画面に出ている版 (download に渡される版) とは取り違えない。
    found: Mutex<HashMap<String, (velopack::UpdateManager, velopack::UpdateInfo)>>,
}

impl VelopackBackend {
    pub fn new(repo_url: &str) -> Self {
        VelopackBackend { repo_url: repo_url.to_string(), found: Mutex::new(HashMap::new()) }
    }

    fn found(&self, version: &str) -> Option<(velopack::UpdateManager, velopack::UpdateInfo)> {
        lock(&self.found).get(version).cloned()
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
                lock(&self.found).insert(offer.version.clone(), (manager, *info));
                Ok(Some(offer))
            }
            _ => Ok(None),
        }
    }

    fn download(&self, version: &str, progress: Progress) -> Result<(), String> {
        let (manager, info) = self.found(version).ok_or_else(|| format!("no update {version} to download"))?;
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

    fn apply_after_exit(&self, version: &str, restart: bool) -> Result<(), String> {
        let (manager, info) = self.found(version).ok_or_else(|| format!("no downloaded update {version}"))?;
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
    use std::sync::mpsc::{Receiver, Sender};
    use std::time::Instant;

    struct FakeBackend {
        installed: bool,
        version: String,
        check_result: Mutex<Result<Option<Offer>, String>>,
        download_result: Mutex<Result<(), String>>,
        progress_steps: Vec<u8>,
        last_query: Mutex<Option<UpdateQuery>>,
        download_calls: Mutex<u32>,
        downloaded: Mutex<Vec<String>>,
        applied: Mutex<Vec<(String, bool)>>,
        /// あると、次の check はこれに何か届くか送り手が消えるまで止まる (通信が止まった状態)。
        check_gate: Mutex<Option<Receiver<()>>>,
        /// check が終わるたびに知らせる。
        check_done: Mutex<Option<Sender<()>>>,
        /// あると、次の download は進み具合を出したあとで止まる。
        download_gate: Mutex<Option<Receiver<()>>>,
        check_panics: bool,
        download_panics: bool,
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
                downloaded: Mutex::new(Vec::new()),
                applied: Mutex::new(Vec::new()),
                check_gate: Mutex::new(None),
                check_done: Mutex::new(None),
                download_gate: Mutex::new(None),
                check_panics: false,
                download_panics: false,
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
            if self.check_panics {
                panic!("fake check blew up");
            }
            let gate = self.check_gate.lock().unwrap().take();
            if let Some(gate) = gate {
                let _ = gate.recv();
            }
            let result = self.check_result.lock().unwrap().clone();
            if let Some(done) = self.check_done.lock().unwrap().as_ref() {
                let _ = done.send(());
            }
            result
        }
        fn download(&self, version: &str, progress: Progress) -> Result<(), String> {
            *self.download_calls.lock().unwrap() += 1;
            self.downloaded.lock().unwrap().push(version.to_string());
            if self.download_panics {
                panic!("fake download blew up");
            }
            for step in &self.progress_steps {
                progress(*step);
            }
            let gate = self.download_gate.lock().unwrap().take();
            if let Some(gate) = gate {
                let _ = gate.recv();
            }
            self.download_result.lock().unwrap().clone()
        }
        fn apply_after_exit(&self, version: &str, restart: bool) -> Result<(), String> {
            self.applied.lock().unwrap().push((version.to_string(), restart));
            Ok(())
        }
    }

    fn updater_with(backend: FakeBackend) -> (Updater, Arc<FakeBackend>, Arc<Mutex<Vec<UpdateState>>>) {
        updater_with_timeout(backend, Duration::from_secs(10))
    }

    fn updater_with_timeout(
        backend: FakeBackend,
        timeout: Duration,
    ) -> (Updater, Arc<FakeBackend>, Arc<Mutex<Vec<UpdateState>>>) {
        let backend = Arc::new(backend);
        let updater = Updater::with_check_timeout(backend.clone(), timeout);
        let seen = Arc::new(Mutex::new(Vec::new()));
        let sink = seen.clone();
        updater.set_emitter(Arc::new(move |state| sink.lock().unwrap().push(state.clone())));
        (updater, backend, seen)
    }

    /// 条件が成り立つまで待つ (最長 5 秒)。
    fn wait_for(what: &str, condition: impl Fn() -> bool) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !condition() {
            assert!(Instant::now() < deadline, "timed out waiting for {what}");
            std::thread::sleep(Duration::from_millis(5));
        }
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
    fn manual_check_that_hangs_times_out_as_failed() {
        let fake = FakeBackend::new();
        let (_hold, gate) = mpsc::channel::<()>();
        *fake.check_gate.lock().unwrap() = Some(gate);
        let (updater, _, _) = updater_with_timeout(fake, Duration::from_millis(100));
        let started = Instant::now();
        updater.check("stable", true);
        assert!(started.elapsed() < Duration::from_secs(5));
        assert_eq!(
            updater.state(),
            UpdateState::Failed {
                stage: FailedStage::Check,
                message: CHECK_TIMEOUT_MESSAGE.into(),
                version: None
            }
        );
    }

    #[test]
    fn automatic_check_that_hangs_goes_back_to_idle() {
        let fake = FakeBackend::new();
        let (_hold, gate) = mpsc::channel::<()>();
        *fake.check_gate.lock().unwrap() = Some(gate);
        let (updater, _, seen) = updater_with_timeout(fake, Duration::from_millis(100));
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::Idle);
        assert_eq!(*seen.lock().unwrap(), vec![UpdateState::Checking, UpdateState::Idle]);
    }

    #[test]
    fn a_hung_check_does_not_block_the_next_one_and_its_late_result_is_dropped() {
        let fake = FakeBackend::new();
        let (release, gate) = mpsc::channel::<()>();
        let (done_sender, done) = mpsc::channel::<()>();
        *fake.check_gate.lock().unwrap() = Some(gate);
        *fake.check_done.lock().unwrap() = Some(done_sender);
        let (updater, backend, _) = updater_with_timeout(fake, Duration::from_millis(100));

        updater.check("beta", true);
        assert!(matches!(updater.state(), UpdateState::Failed { stage: FailedStage::Check, .. }));

        // 止まった確認が残っていても、もう一度確かめられる。
        *backend.check_result.lock().unwrap() = Ok(None);
        updater.check("stable", true);
        assert_eq!(updater.state(), UpdateState::UpToDate);
        done.recv_timeout(Duration::from_secs(5)).expect("second check finished");

        // 止まっていた確認があとで結果 (新しい版) を返しても、状態は変わらない。
        *backend.check_result.lock().unwrap() = Ok(Some(offer("9.9.9")));
        release.send(()).unwrap();
        done.recv_timeout(Duration::from_secs(5)).expect("hung check finished");
        std::thread::sleep(Duration::from_millis(50));
        assert_eq!(updater.state(), UpdateState::UpToDate);
    }

    #[test]
    fn a_panicking_check_ends_in_failed_or_idle() {
        let mut fake = FakeBackend::new();
        fake.check_panics = true;
        let (updater, _, _) = updater_with(fake);
        updater.check("stable", true);
        match updater.state() {
            UpdateState::Failed { stage: FailedStage::Check, message, version: None } => {
                assert!(message.contains("fake check blew up"), "{message}")
            }
            other => panic!("unexpected state {other:?}"),
        }
        updater.check("stable", false);
        assert_eq!(updater.state(), UpdateState::Idle);
    }

    #[test]
    fn download_reports_progress_then_ready() {
        let (updater, backend, seen) = updater_with(FakeBackend::new());
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
        // 画面に出した版を落とす。
        assert_eq!(*backend.downloaded.lock().unwrap(), vec!["3.6.0".to_string()]);
    }

    #[test]
    fn downloading_state_holds_while_the_download_runs() {
        let mut fake = FakeBackend::new();
        fake.progress_steps = vec![10, 50];
        let (release, gate) = mpsc::channel::<()>();
        *fake.download_gate.lock().unwrap() = Some(gate);
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", false);

        let worker = updater.clone();
        let download = std::thread::spawn(move || worker.download());
        let downloading = UpdateState::Downloading { version: "3.6.0".into(), percent: 50 };
        wait_for("downloading 50%", || updater.state() == downloading);

        // ダウンロード中は、確認・2 回目のダウンロード・再起動の依頼をどれも受け付けない。
        *backend.last_query.lock().unwrap() = None;
        updater.check("beta", true);
        updater.download();
        assert!(!updater.request_restart());
        updater.apply_on_exit();
        assert_eq!(updater.state(), downloading);
        assert!(backend.last_query.lock().unwrap().is_none());
        assert_eq!(*backend.download_calls.lock().unwrap(), 1);
        assert!(backend.applied.lock().unwrap().is_empty());

        release.send(()).unwrap();
        download.join().unwrap();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
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
        assert_eq!(*backend.downloaded.lock().unwrap(), vec!["3.6.0".to_string(), "3.6.0".to_string()]);
    }

    #[test]
    fn a_panicking_download_ends_in_failed_and_can_be_retried() {
        let mut fake = FakeBackend::new();
        fake.download_panics = true;
        let (updater, backend, _) = updater_with(fake);
        updater.check("stable", false);
        updater.download();
        match updater.state() {
            UpdateState::Failed { stage: FailedStage::Download, message, version } => {
                assert!(message.contains("fake download blew up"), "{message}");
                assert_eq!(version, Some("3.6.0".into()));
            }
            other => panic!("unexpected state {other:?}"),
        }
        updater.download();
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
        assert_eq!(*backend.applied.lock().unwrap(), vec![("3.6.0".to_string(), false)]);
    }

    #[test]
    fn skipped_apply_on_exit_does_nothing_even_when_ready() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        updater.check("stable", false);
        updater.skip_apply_on_exit();
        updater.download();
        assert_eq!(updater.state(), UpdateState::Ready { version: "3.6.0".into(), restart_requested: false });
        updater.apply_on_exit();
        assert!(backend.applied.lock().unwrap().is_empty());
    }

    #[test]
    fn restart_now_is_passed_to_apply() {
        let (updater, backend, _) = updater_with(FakeBackend::new());
        assert!(!updater.request_restart());
        updater.check("stable", false);
        updater.download();
        assert!(updater.request_restart());
        updater.apply_on_exit();
        assert_eq!(*backend.applied.lock().unwrap(), vec![("3.6.0".to_string(), true)]);
    }

    #[test]
    fn state_json_shape_covers_every_variant() {
        use serde_json::json;
        let cases = [
            (UpdateState::NotInstalled, json!({"status": "not_installed"})),
            (UpdateState::Idle, json!({"status": "idle"})),
            (UpdateState::Checking, json!({"status": "checking"})),
            (UpdateState::UpToDate, json!({"status": "up_to_date"})),
            (
                UpdateState::Available { version: "3.6.0".into(), size_bytes: 1234, is_downgrade: true },
                json!({"status": "available", "version": "3.6.0", "size_bytes": 1234, "is_downgrade": true}),
            ),
            (
                UpdateState::Downloading { version: "3.6.0".into(), percent: 5 },
                json!({"status": "downloading", "version": "3.6.0", "percent": 5}),
            ),
            (
                UpdateState::Ready { version: "3.6.0".into(), restart_requested: true },
                json!({"status": "ready", "version": "3.6.0", "restart_requested": true}),
            ),
            (
                UpdateState::Failed { stage: FailedStage::Check, message: "x".into(), version: None },
                json!({"status": "failed", "stage": "check", "message": "x", "version": null}),
            ),
            (
                UpdateState::Failed {
                    stage: FailedStage::Download,
                    message: "y".into(),
                    version: Some("3.6.0".into()),
                },
                json!({"status": "failed", "stage": "download", "message": "y", "version": "3.6.0"}),
            ),
        ];
        for (state, expected) in cases {
            assert_eq!(serde_json::to_value(&state).unwrap(), expected, "{state:?}");
        }
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
