import os, subprocess, shutil, logging
import requests, json
from typing import List, Dict, Optional
from pathlib import Path

from dmoj.executors.c_like_executor import CExecutor, GCCMixin
from dmoj.cptbox.filesystem_policies import RecursiveDir, ExactFile
from dmoj.result import Result

log = logging.getLogger('dmoj.executors.VLOG')

class Executor(GCCMixin, CExecutor):
    name = 'Verilog (Icarus)'
    ext = '.v'
    command = 'iverilog'
    run_command = 'vvp'
    flags = ['-g2012']
    arch = ''
    fsize = 50 * 1024 * 1024  # 50 MiB
    wave_dest = Path('/waves')
    f4pga_url = "https://fpga.gai.tw:5280/api/fpga/compile_form"
    openlane_url = "https://fpga.gai.tw:5280/api/openlane/flow"
    
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
        
        if self._dir:
            self._dump_artifacts()
            self._process_waveform(result)
            self._process_ppa(result)       
        
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

    def _process_waveform(self, result):
        
        enable_waveform = self.meta.get('enable_waveform', False)
        log.info("Waveform processing enabled: %s", enable_waveform) 
        
        if not enable_waveform:
            log.info("Waveform processing disabled for this problem")
            return
        
        vcd_path = os.path.join(self._dir, 'wave.vcd')
        if not os.path.exists(vcd_path):
            log.info("No wave.vcd file found, skipping waveform processing")
            return
        
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
    
    def _process_ppa(self, result):
        enable_ppa = self.meta.get('enable_ppa', False)
        log.info("PPA processing enabled: %s", enable_ppa)
        
        if not enable_ppa:
            log.info("PPA processing disabled for this problem")
            return
        
        # 收集所有 meta 設定
        f4pga_board = self.meta.get('f4pga_board', None)
        f4pga_target_fmax = self.meta.get('f4pga_target_fmax', None)
        
        openlane_pdk = self.meta.get('openlane_pdk', None)
        openlane_ppa_score = self.meta.get('openlane_ppa_score', None)
        openlane_critical_path_ns = self.meta.get('openlane_critical_path_ns', None)
        openlane_core_area_um2 = self.meta.get('openlane_core_area_um2', None)
        openlane_power_total = self.meta.get('openlane_power_total', None)
        
        log.info("F4PGA board: %s, target_fmax: %s", f4pga_board, f4pga_target_fmax)
        log.info("OpenLane PDK: %s", openlane_pdk)
        log.info("OpenLane metrics - ppa_score: %s, critical_path_ns: %s, core_area_um2: %s, power_total: %s", 
                openlane_ppa_score, openlane_critical_path_ns, openlane_core_area_um2, openlane_power_total)
        
        # 收集所有回饋訊息
        feedback_parts = []
        failed_targets = []
        
        # 執行 F4PGA（如果有設定板子）
        if f4pga_board:
            f4pga_result = self._execute_f4pga(f4pga_board, result)
            if f4pga_result:
                f4pga_failed = self._check_f4pga_targets(f4pga_result, f4pga_target_fmax, feedback_parts)
                if f4pga_failed:
                    failed_targets.extend(f4pga_failed)
            else:
                feedback_parts.append("[F4PGA] Execution failed")
        
        # 執行 OpenLane（如果有設定 PDK）
        if openlane_pdk:
            openlane_result = self._execute_openlane(openlane_pdk, result)
            if openlane_result:
                openlane_failed = self._check_openlane_targets(openlane_result, openlane_ppa_score, 
                                                            openlane_critical_path_ns, openlane_core_area_um2, 
                                                            openlane_power_total, feedback_parts)
                if openlane_failed:
                    failed_targets.extend(openlane_failed)
            else:
                feedback_parts.append("[OpenLane] Execution failed")
        
        # 檢查是否有任何 API 被執行
        if not f4pga_board and not openlane_pdk:
            log.info("PPA enabled but no board/PDK selected, skipping PPA processing")
            feedback_parts.append("[PPA] No board/PDK selected")
        
        # 統一評估結果
        if failed_targets:
            log.info(f"PPA 目標未達標: {failed_targets}")
            result.result_flag = Result.PLE
            result.points = 0
            # feedback 用簡短摘要（避免資料庫長度限制）
            result.feedback = f"PPA targets not met ({len(failed_targets)} items)"
            log.info(f"設定簡短 feedback: {result.feedback}")
            # 詳細內容放到 extended_feedback
            feedback_parts.append(f"[PPA] FAILED: {'; '.join(failed_targets)}")
        else:
            log.info("所有 PPA 目標都達標")
            if f4pga_board or openlane_pdk:
                feedback_parts.append("[PPA] All targets passed")
        
        # 將所有回饋訊息加入 extended_feedback
        if feedback_parts:
            current_feedback = result.extended_feedback or ''
            result.extended_feedback = current_feedback + '\n' + '\n'.join(feedback_parts)

    def _execute_f4pga(self, f4pga_board, result):
        """執行 F4PGA API，返回結果資料或 None"""
        log.info("Executing F4PGA for board: %s", f4pga_board)
        
        verilog_files = [f for f in os.listdir(self._dir) if f.endswith('.v')]
        if not verilog_files:
            log.warning("No Verilog files found for F4PGA processing")
            return None
        
        src_code = verilog_files[0]
        xdc_filename = f"{self.problem}_{f4pga_board}.xdc"
        xdc_path = os.path.join(self._dir, xdc_filename)
        
        if not os.path.exists(xdc_path):
            log.error(f"F4PGA XDC file not found: {xdc_filename}")
            result.result_flag = Result.IE
            result.feedback = f"F4PGA XDC file not found: {xdc_filename}"
            return None
        
        try:
            files = {
                "verilog_file": open(os.path.join(self._dir, src_code), "rb"),
                "xdc_file": open(xdc_path, "rb")
            }
            data = {
                "fpga_target": f4pga_board
            }
            
            log.info(f"正在連接到 F4PGA 服務: {self.f4pga_url}")
            response = requests.post(self.f4pga_url, files=files, data=data)
            
            # 關閉檔案
            files["verilog_file"].close()
            files["xdc_file"].close()

            log.info(f"F4PGA HTTP 狀態碼: {response.status_code}")

            if response.status_code != 200:
                log.error(f"F4PGA 伺服器回傳錯誤狀態碼: {response.status_code}")
                return None

            if not response.text.strip():
                log.error("F4PGA 伺服器回傳空內容")
                return None

            # 解析 JSON 回應
            result_json = response.json()
            log.info(f"F4PGA 回應成功")
            return result_json
            
        except FileNotFoundError as e:
            log.error(f"F4PGA 檔案未找到: {e}")
            result.result_flag = Result.IE
            result.feedback = f"F4PGA file not found: {str(e)}"
            return None
        except Exception as e:
            log.error(f"F4PGA 執行錯誤: {e}")
            return None

    def _execute_openlane(self, openlane_pdk, result):
        """執行 OpenLane API，返回結果資料或 None"""
        log.info("Executing OpenLane for PDK: %s", openlane_pdk)
        
        verilog_files = [f for f in os.listdir(self._dir) if f.endswith('.v')]
        if not verilog_files:
            log.warning("No Verilog files found for OpenLane processing")
            return None
        
        src_code = verilog_files[0]
        config_filename = f"{self.problem}_{openlane_pdk}_config.json"
        config_path = os.path.join(self._dir, config_filename)
        
        if not os.path.exists(config_path):
            log.error(f"OpenLane config file not found: {config_filename}")
            result.result_flag = Result.IE
            result.feedback = f"OpenLane config file not found: {config_filename}"
            return None
        
        try:
            files = {
                "verilog_file": open(os.path.join(self._dir, src_code), "rb"),
                "config_file": open(config_path, "rb")
            }
            
            log.info(f"正在連接到 OpenLane 服務: {self.openlane_url}")
            response = requests.post(self.openlane_url, files=files)
            
            # 關閉檔案
            files["verilog_file"].close()
            files["config_file"].close()

            log.info(f"OpenLane HTTP 狀態碼: {response.status_code}")

            if response.status_code != 200:
                log.error(f"OpenLane 伺服器回傳錯誤狀態碼: {response.status_code}")
                return None

            if not response.text.strip():
                log.error("OpenLane 伺服器回傳空內容")
                return None

            # 解析 JSON 回應
            result_json = response.json()
            
            # 檢查是否成功
            if result_json.get('success') != True:
                log.error(f"OpenLane 合成失敗: {result_json}")
                return None
            
            log.info(f"OpenLane 回應成功")
            return result_json
            
        except Exception as e:
            log.error(f"OpenLane 執行錯誤: {e}")
            return None

    def _check_f4pga_targets(self, f4pga_data, f4pga_target_fmax, feedback_parts):
        """檢查 F4PGA 目標，返回失敗的目標列表"""
        failed = []
        
        try:
            fmax_value = json.loads(f4pga_data["PPA"])["Fmax"]
            log.info(f"F4PGA Fmax: {fmax_value}")
            
            # 構建 F4PGA 回饋訊息
            if f4pga_target_fmax is not None:
                feedback_parts.append(f"[F4PGA] Fmax: {fmax_value} (Target: {f4pga_target_fmax} MHz)")
            else:
                feedback_parts.append(f"[F4PGA] Fmax: {fmax_value}")
            
            # 檢查目標
            if f4pga_target_fmax is not None and fmax_value is not None:
                try:
                    if fmax_value < f4pga_target_fmax:
                        failed.append(f"F4PGA Fmax {fmax_value} MHz < {f4pga_target_fmax} MHz")
                except (ValueError, TypeError):
                    log.error(f"無法解析 F4PGA Fmax: {fmax_value}")
            
        except Exception as e:
            log.error(f"檢查 F4PGA 目標時錯誤: {e}")
            feedback_parts.append("[F4PGA] Error processing results")
        
        return failed

    def _check_openlane_targets(self, openlane_data, openlane_ppa_score, openlane_critical_path_ns, 
                            openlane_core_area_um2, openlane_power_total, feedback_parts):
        """檢查 OpenLane 目標，返回失敗的目標列表"""
        failed = []
        
        try:
            # 提取各個指標
            actual_critical_path_ns = json.loads(openlane_data["PPA"])["critical_path_ns"]
            actual_core_area_um2 = json.loads(openlane_data["PPA"])["CoreArea_um2"]
            actual_power_total = json.loads(openlane_data["PPA"])["power_total"]
            actual_ppa_score = json.loads(openlane_data["PPA"])["ppa_score"]
            
            log.info(f"OpenLane 實際值: PPA={actual_ppa_score}, Path={actual_critical_path_ns}, Area={actual_core_area_um2}, Power={actual_power_total}")
            
            # 構建詳細的 OpenLane 回饋訊息
            openlane_details = []
            
            if openlane_ppa_score is not None:
                openlane_details.append(f"PPA Score: {actual_ppa_score} (Target: ≥{openlane_ppa_score})")
            else:
                openlane_details.append(f"PPA Score: {actual_ppa_score}")
                
            if openlane_critical_path_ns is not None:
                openlane_details.append(f"Critical Path: {actual_critical_path_ns}ns (Max: {openlane_critical_path_ns}ns)")
            else:
                openlane_details.append(f"Critical Path: {actual_critical_path_ns}ns")
                
            if openlane_core_area_um2 is not None:
                openlane_details.append(f"Core Area: {actual_core_area_um2}μm² (Max: {openlane_core_area_um2}μm²)")
            else:
                openlane_details.append(f"Core Area: {actual_core_area_um2}μm²")
                
            if openlane_power_total is not None:
                openlane_details.append(f"Power: {actual_power_total}mW (Max: {openlane_power_total}mW)")
            else:
                openlane_details.append(f"Power: {actual_power_total}mW")
            
            feedback_parts.append(f"[OpenLane] {', '.join(openlane_details)}")
            
            # 檢查各個目標
            if openlane_ppa_score is not None and actual_ppa_score != "N/A":
                try:
                    if actual_ppa_score < openlane_ppa_score:
                        failed.append(f"OpenLane PPA score {actual_ppa_score} < {openlane_ppa_score}")
                except (ValueError, TypeError):
                    pass
            
            if openlane_critical_path_ns is not None and actual_critical_path_ns != "N/A":
                try:
                    if actual_critical_path_ns > openlane_critical_path_ns:
                        failed.append(f"OpenLane critical path {actual_critical_path_ns}ns > {openlane_critical_path_ns}ns")
                except (ValueError, TypeError):
                    pass
            
            if openlane_core_area_um2 is not None and actual_core_area_um2 != "N/A":
                try:
                    if actual_core_area_um2 > openlane_core_area_um2:
                        failed.append(f"OpenLane core area {actual_core_area_um2}μm² > {openlane_core_area_um2}μm²")
                except (ValueError, TypeError):
                    pass
            
            if openlane_power_total is not None and actual_power_total != "N/A":
                try:
                    if actual_power_total > openlane_power_total:
                        failed.append(f"OpenLane power {actual_power_total}mW > {openlane_power_total}mW")
                except (ValueError, TypeError):
                    pass
            
        except Exception as e:
            log.error(f"檢查 OpenLane 目標時錯誤: {e}")
            feedback_parts.append("[OpenLane] Error processing results")
        
        return failed
                
    
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
