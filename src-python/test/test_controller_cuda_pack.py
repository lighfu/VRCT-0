"""GPU 部品のエンドポイント、導入後の GPU への切り替え、エラーの送り方のテスト。"""

import copy
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import controller as controller_module
import mainloop
from controller import Controller
from errors import ErrorCode, ERROR_METADATA

CPU = {"device": "cpu", "device_index": 0, "device_name": "cpu", "compute_types": ["auto", "int8"]}
GPU = {"device": "cuda", "device_index": 0, "device_name": "NVIDIA GeForce RTX 3070", "compute_types": ["auto", "float16"]}


def _config(**overrides):
    values = dict(
        CUDA_PACK_PROMPTED=False,
        CUDA_PACK_SELECT_GPU_ON_NEXT_START=False,
        SELECTABLE_COMPUTE_DEVICE_LIST=[CPU, GPU],
        SELECTED_TRANSLATION_COMPUTE_DEVICE=copy.deepcopy(CPU),
        SELECTED_TRANSLATION_COMPUTE_TYPE="int8",
        SELECTED_TRANSCRIPTION_COMPUTE_DEVICE=copy.deepcopy(CPU),
        SELECTED_TRANSCRIPTION_COMPUTE_TYPE="int8",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class CudaPackControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = MagicMock()
        self.model.cudaPackStatus.return_value = "not_installed"
        self.config = _config()
        for target, value in (("model", self.model), ("config", self.config)):
            patcher = patch.object(controller_module, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Controller()
        self.controller.setRunMapping(mainloop.run_mapping)
        self.run = MagicMock()
        self.controller.setRun(self.run)

    def _sent(self, endpoint):
        return [call.args for call in self.run.call_args_list if call.args[1] == endpoint]

    def test_status_payload(self) -> None:
        self.config.CUDA_PACK_PROMPTED = True
        self.assertEqual(
            self.controller.getCudaPackStatus(),
            {"status": 200, "result": {"status": "not_installed", "prompted": True}},
        )

    def test_download_starts_one_thread(self) -> None:
        with patch.object(controller_module, "Thread") as mock_thread:
            self.controller.downloadCudaPack()
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["target"], self.model.downloadCudaPack)
        self.model.cudaPackStatus.assert_called_with(downloading=True)
        self.assertTrue(self._sent("/run/cuda_pack_status"))

    def test_second_download_request_is_ignored(self) -> None:
        with patch.object(controller_module, "Thread") as mock_thread:
            self.controller.downloadCudaPack()
            self.controller.downloadCudaPack()
        mock_thread.assert_called_once()

    def test_download_is_refused_unless_not_installed(self) -> None:
        self.model.cudaPackStatus.return_value = "installed"
        with patch.object(controller_module, "Thread") as mock_thread:
            self.controller.downloadCudaPack()
        mock_thread.assert_not_called()

    def test_successful_download_schedules_the_gpu_switch(self) -> None:
        self.model.isCudaPackInstalled.return_value = True
        with patch.object(controller_module, "Thread"):
            self.controller.downloadCudaPack()
        self.controller.finishCudaPackDownload()
        self.assertTrue(self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START)
        self.assertEqual(self._sent("/run/downloaded_cuda_pack"), [(200, "/run/downloaded_cuda_pack", True)])
        self.assertTrue(self._sent("/run/cuda_pack_status"))

    def test_failed_download_reports_error_and_status(self) -> None:
        self.model.isCudaPackInstalled.return_value = False
        with patch.object(controller_module, "Thread"):
            self.controller.downloadCudaPack()
        self.run.reset_mock()
        self.controller.finishCudaPackDownload()
        errors = self._sent("/run/error_cuda_pack")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 400)
        self.assertEqual(errors[0][2]["error_code"], "CUDA_PACK_DOWNLOAD")
        self.assertFalse(self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START)
        self.model.cudaPackStatus.assert_called_with(downloading=False)
        self.assertTrue(self._sent("/run/cuda_pack_status"))

    def test_remove_requests_removal_only_when_installed(self) -> None:
        self.model.cudaPackStatus.return_value = "installed"
        self.controller.removeCudaPack()
        self.model.requestCudaPackRemoval.assert_called_once()
        self.model.requestCudaPackRemoval.reset_mock()
        self.model.cudaPackStatus.return_value = "not_installed"
        self.controller.removeCudaPack()
        self.model.requestCudaPackRemoval.assert_not_called()

    def test_mark_prompted(self) -> None:
        self.controller.markCudaPackPrompted()
        self.assertTrue(self.config.CUDA_PACK_PROMPTED)

    def test_gpu_is_selected_after_install(self) -> None:
        self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START = True
        self.controller.applyCudaPackGpuSelection()
        self.assertEqual(self.config.SELECTED_TRANSLATION_COMPUTE_DEVICE, GPU)
        self.assertEqual(self.config.SELECTED_TRANSCRIPTION_COMPUTE_DEVICE, GPU)
        self.assertEqual(self.config.SELECTED_TRANSLATION_COMPUTE_TYPE, "auto")
        self.assertEqual(self.config.SELECTED_TRANSCRIPTION_COMPUTE_TYPE, "auto")
        self.assertFalse(self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START)
        self.controller.reportCudaPackNotLoaded()
        self.assertEqual(self._sent("/run/error_cuda_pack"), [])

    def test_gpu_missing_after_install_reports_once(self) -> None:
        self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START = True
        self.config.SELECTABLE_COMPUTE_DEVICE_LIST = [CPU]
        self.controller.applyCudaPackGpuSelection()
        self.assertEqual(self.config.SELECTED_TRANSLATION_COMPUTE_DEVICE, CPU)
        self.assertFalse(self.config.CUDA_PACK_SELECT_GPU_ON_NEXT_START)
        self.controller.reportCudaPackNotLoaded()
        self.controller.reportCudaPackNotLoaded()
        errors = self._sent("/run/error_cuda_pack")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][2]["error_code"], "CUDA_PACK_NOT_LOADED")

    def test_nothing_happens_without_the_flag(self) -> None:
        self.controller.applyCudaPackGpuSelection()
        self.assertEqual(self.config.SELECTED_TRANSLATION_COMPUTE_DEVICE, CPU)


class WiringTests(unittest.TestCase):
    def test_endpoints_are_registered(self) -> None:
        for endpoint in ("/get/data/cuda_pack_status", "/run/download_cuda_pack", "/run/remove_cuda_pack", "/run/mark_cuda_pack_prompted"):
            self.assertIn(endpoint, mainloop.mapping)
        for key in ("cuda_pack_status", "download_progress_cuda_pack", "downloaded_cuda_pack", "error_cuda_pack"):
            self.assertIn(key, mainloop.run_mapping)
        self.assertIn("/get/data/cuda_pack_status", mainloop.init_mapping)

    def test_error_codes_exist(self) -> None:
        for code in (ErrorCode.CUDA_PACK_DOWNLOAD, ErrorCode.CUDA_PACK_NOT_LOADED):
            self.assertIn(code, ERROR_METADATA)

    def test_init_switches_before_engines_and_reports_after_settings(self) -> None:
        source = inspect.getsource(Controller.init)
        self.assertLess(source.index("applyCudaPackGpuSelection"), source.index("Set Translation Engine"))
        self.assertLess(source.index("self.updateConfigSettings()"), source.index("reportCudaPackNotLoaded"))
