import os, subprocess, shutil, logging
import requests
from typing import List, Dict, Optional
from pathlib import Path

from dmoj.executors.c_like_executor import CExecutor, GCCMixin
from dmoj.cptbox.filesystem_policies import RecursiveDir, ExactFile   # <<< 這行要加

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
            
            # 波型
            vcd_path = os.path.join(self._dir, 'wave.vcd')
            if os.path.exists(vcd_path):
                with open(vcd_path, 'rb') as f:
                    head = f.read(128)
                
                # 檢查VCD檔案是否有效
                vcd_size = os.path.getsize(vcd_path)
                log.info("VCD file size: %d bytes", vcd_size)
                
                if vcd_size < 100:
                    log.warning("VCD file is too small, may be invalid")
                    return
                
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
