import os, subprocess, shutil, logging
import requests
from typing import List, Dict, Optional
from pathlib import Path

from dmoj.executors.c_like_executor import CExecutor, GCCMixin
from dmoj.cptbox.filesystem_policies import RecursiveDir, ExactFile   # <<< 這行要加
from dmoj.result import Result

log = logging.getLogger('dmoj.executors.VLOG')   # 名稱隨你取，只要與 judge logger 層級對得上

class Executor(GCCMixin, CExecutor):
    name = 'Verilog (Icarus)'
    ext = '.v'
    command = 'iverilog'
    run_command = 'vvp'
    flags = ['-g2012']
    arch = ''
    fsize = 50 * 1024 * 1024  # 50 MiB
    wave_dest = Path('/waves')
    url = "https://fpga.gai.tw:5280/api/fpga/compile_form"
    
    # 允許網路相關的系統呼叫，讓 requests 能正常運作
    syscalls = [
        'socket',        # 建立 socket
        'connect',       # 連接到伺服器
        'sendto',        # 發送資料
        'recvfrom',      # 接收資料
        'send',          # 發送資料（另一種方式）
        'recv',          # 接收資料（另一種方式）
        'setsockopt',    # 設定 socket 選項
        'getsockopt',    # 取得 socket 選項
        'shutdown',      # 關閉 socket
    ]
    
    def __init__(self, problem_id, source_code, *, meta=None, submission_id=None, **kwargs):
        super().__init__(problem_id, source_code, **kwargs)
        self.meta = meta or {}
        self.submission_id = submission_id
        log.info("Initializing Verilog executor with submission_id: %r", self.submission_id)
    
    def populate_result(self, stderr, result, process):
        super().populate_result(stderr, result, process)
        print("Result after populate:", result)
        
        # 調試：印出submission_id
        log.info("=== VLOG Executor Debug ===")
        log.info("self.submission_id: %r", self.submission_id)
        log.info("self.meta: %r", self.meta)  # 添加這行來調試 meta 資料
        log.info("Initial result_flag after super(): %d", result.result_flag)
        
        if self._dir:
            self._dump_artifacts()
            
            # 檢查是否啟用波型處理
            enable_waveform = self.meta.get('enable_waveform', False)
            log.info("Waveform processing enabled: %s", enable_waveform)
            
            # 波型處理 (只有在題目設定啟用時才處理)
            if enable_waveform:
                vcd_path = os.path.join(self._dir, 'wave.vcd')
                if os.path.exists(vcd_path):
                    with open(vcd_path, 'rb') as f:
                        head = f.read(128)
                    
                    # 檢查VCD檔案是否有效
                    vcd_size = os.path.getsize(vcd_path)
                    log.info("VCD file size: %d bytes", vcd_size)
                    
                    if vcd_size < 100:
                        log.warning("VCD file is too small, may be invalid")
                    else:
                        # 使用傳遞的submission_id
                        submission_id = self.submission_id or 'unknown'
                        
                        log.info("wave.vcd head: %r", head)
                        log.info("submission_id: %s", submission_id)
                        
                        # 確保 /waves 目錄存在
                        self.wave_dest.mkdir(parents=True, exist_ok=True)
                        
                        # 使用 submission_id 命名 VCD 檔案
                        vcd_dest = f'/waves/{submission_id}.vcd'
                        
                        try:
                            # 複製 VCD 檔案到 /waves 目錄
                            shutil.copy(vcd_path, vcd_dest)
                            log.info('Saved VCD to %s', vcd_dest)
                            
                            # 將VCD檔案路徑加入 extended_feedback
                            wave_info = f'\n[WAVEFORM]VCD:{vcd_dest}'
                            result.extended_feedback = (result.extended_feedback or '') + wave_info
                            
                        except Exception as e:
                            log.error("Failed to copy VCD file: %s", e)
                else:
                    log.info("No wave.vcd file found, skipping waveform processing")
            else:
                log.info("Waveform processing disabled for this problem")
            
            # 檢查是否啟用 PPA 計算
            enable_ppa = self.meta.get('enable_ppa', False)
            log.info("PPA calculation enabled: %s", enable_ppa)
            
            # PPA 處理 (只有在題目設定啟用時才處理)
            if enable_ppa:
                verilog_files = [f for f in os.listdir(self._dir) if f.endswith('.v')]
                if verilog_files:
                    src_code = verilog_files[0]
                else:
                    src_code = None
                
                if src_code:
                    files = {
                        "verilog_file": open(self._dir + "/" + src_code, "rb"),
                        "xdc_file": open(self._dir + "/" + self.problem + ".xdc", "rb")
                    }
                    data = {
                    "fpga_target": "basys3"
                }
                log.info(f"正在連接到: {self.url}")

                try:
                    # 發送 POST 請求
                    response = requests.post(self.url, files=files, data=data)
                except requests.exceptions.ConnectionError:
                    log.error("連接錯誤：無法連接到伺服器。請確認伺服器是否正在運行。")
                    files["verilog_file"].close()
                    files["xdc_file"].close()
                    return
                except requests.exceptions.Timeout:
                    log.error("請求超時：伺服器回應太慢。")
                    files["verilog_file"].close()
                    files["xdc_file"].close()
                    return
                except requests.exceptions.RequestException as e:
                    log.error(f"請求錯誤：{e}")
                    files["verilog_file"].close()
                    files["xdc_file"].close()
                    return

                # 關閉檔案
                files["verilog_file"].close()
                files["xdc_file"].close()

                # 檢查回應狀態
                log.info(f"HTTP 狀態碼: {response.status_code}")

                if response.status_code != 200:
                    log.error(f"伺服器回傳錯誤狀態碼: {response.status_code}")
                    log.error(f"錯誤內容: {response.text}")
                    return

                # 檢查回應內容是否為空
                if not response.text.strip():
                    log.error("伺服器回傳空內容")
                    return

                # 嘗試解析 JSON
                try:
                    result_json = response.json()
                except Exception as e:
                    log.error(f"JSON 解析錯誤: {e}")
                    log.error(f"伺服器回應內容: {response.text}")
                    log.error("這可能表示伺服器沒有正常運行或回傳了非 JSON 格式的內容")
                    return

                # 嘗試解析 JSON 並提取 Fmax 數值
                try:
                    # 先取得整個 PPA 物件
                    ppa_data = result_json.get("PPA", {})
                    log.info(f"PPA 整體資料: {repr(ppa_data)}")
                    
                    # 從 PPA 物件中提取 Fmax 數值
                    if isinstance(ppa_data, dict):
                        fmax_value = ppa_data.get("Fmax", "N/A")
                        log.info("PPA 資料是字典格式")
                    else:
                        # 如果 PPA 是字串，嘗試解析 JSON
                        import json
                        try:
                            log.info("嘗試解析 PPA 字串為 JSON")
                            ppa_dict = json.loads(ppa_data)
                            fmax_value = ppa_dict.get("Fmax", "N/A")
                            log.info("JSON 解析成功")
                        except Exception as parse_error:
                            log.error(f"JSON 解析失敗: {parse_error}")
                            fmax_value = "Parse Error"
                    
                    log.info(f"提取的 Fmax 數值: {fmax_value}")
                    
                    # 檢查 PPA 最大頻率限制
                    ppa_maximum_fmax = self.meta.get('ppa_maximum_fmax')
                    if ppa_maximum_fmax is not None and fmax_value != "N/A" and fmax_value != "Parse Error":
                        try:
                            # 提取數值部分（移除可能的單位）
                            fmax_numeric = float(str(fmax_value).replace(' MHz', '').replace('MHz', '').strip())
                            log.info(f"PPA 頻率檢查: 實際值={fmax_numeric} MHz, 最大限制={ppa_maximum_fmax} MHz")
                            
                            if fmax_numeric > ppa_maximum_fmax:
                                log.info(f"PPA 頻率超過限制: {fmax_numeric} > {ppa_maximum_fmax}")
                                # 設定為 PLE (PPA Limit Exceeded) 狀態
                                result.result_flag = Result.PLE
                                result.points = 0  # PLE 狀態應該得 0 分
                                result.feedback = f"PPA Performance limit exceeded: {fmax_numeric} MHz > {ppa_maximum_fmax} MHz (maximum allowed)"
                                log.info("已設定為 PLE 狀態由於 PPA 頻率超過限制")
                                log.info("PLE 設定後的 result_flag: %d", result.result_flag)
                            else:
                                log.info(f"PPA 頻率符合限制: {fmax_numeric} <= {ppa_maximum_fmax}")
                        except (ValueError, TypeError) as e:
                            log.error(f"無法解析 Fmax 數值進行比較: {e}, 原始值: {fmax_value}")
                    else:
                        if ppa_maximum_fmax is None:
                            log.info("未設定 PPA 最大頻率限制，跳過檢查")
                        else:
                            log.info(f"無法進行 PPA 頻率檢查，Fmax 值: {fmax_value}")
                    
                    # 檢查 extended_feedback 當前狀態
                    current_feedback = result.extended_feedback or ''
                    log.info(f"設定前的 extended_feedback: {repr(current_feedback)}")
                    
                    # 顯示格式：符合前端模板的 [PPA] 標籤格式
                    ppa_info = f'\n[PPA] {fmax_value} MHz'
                    result.extended_feedback = current_feedback + ppa_info
                    
                    log.info(f"設定後的 extended_feedback: {repr(result.extended_feedback)}")
                    log.info(f"PPA 資訊已加入: {repr(ppa_info)}")
                    
                except Exception as e:
                    log.error(f"處理 PPA 資料時發生錯誤: {e}")
                    result.extended_feedback = (result.extended_feedback or '') + f'\n[PPA] Error'
                    log.info(f"錯誤處理後的 extended_feedback: {repr(result.extended_feedback)}")
                else:
                    log.info("No Verilog files found, skipping PPA processing")
            else:
                log.info("PPA calculation disabled for this problem")
        
        # 最終調試：檢查 populate_result 結束時的狀態
        log.info("=== Final Result Debug ===")
        log.info("Final result_flag: %d", result.result_flag)
        log.info("Final feedback: %r", result.feedback)
        log.info("Final extended_feedback: %r", result.extended_feedback)
            
    def _dump_artifacts(self):
        try:
            for root, dirs, files in os.walk(self._dir):
                for f in files:
                    p = os.path.join(root, f)
                    size = os.path.getsize(p)
                    log.info("[artifact] %s (%d bytes)", p, size)
        except Exception as e:
            log.warning("dump_artifacts failed: %s", e)

    def get_write_fs(self):
        base = super().get_write_fs()
        # 確保能寫工作目錄
        if self._dir:
            base = base + [RecursiveDir(self._dir)] + [RecursiveDir('/waves')]
        return base

    def get_compile_args(self) -> List[str]:
        # 自動把 .sv 檔加進來
        if 'self_test.v' not in self.source_paths:
            sv_files = [f for f in os.listdir(self._dir) if f.endswith('.sv')]
            if sv_files:
                log.info('Adding SV files: %s', sv_files)
                self.source_paths.extend(sv_files)

        #用log檢查self._dir裡的所有檔案
        log.info('directory contents: %s', os.listdir(self._dir))

        args = [
            self.runtime_dict[self.command],
            *self.flags,
            '-o', self.get_compiled_file(),
            *self.source_paths,
        ]
        log.info('Compile args: %s', args)
        return args

    def get_cmdline(self, **kwargs) -> List[str]:
        cmd = [self.run_command, self.get_compiled_file()]
        log.info('Run cmd: %s', cmd)
        return cmd

    def get_compiled_file(self) -> str:
        return self._file(self._dir + '/' + self.problem)

    @classmethod
    def get_find_first_mapping(cls) -> Optional[Dict[str, List[str]]]:
        return {'iverilog': ['iverilog'], 'vvp': ['vvp']}

    test_program = r"""
    module main;
        integer ch;
        initial begin
            forever begin
                ch = $fgetc(32'h8000_0000);
                if (ch == -1) begin
                    $finish(0);
                end else begin
                    $write("%c", ch);
                end
            end
        end
    endmodule
    """
