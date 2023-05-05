import os
import json
import numpy as np




class MixerCorrection:
    """ A simple class for mixer correction.
    
        Attributes:
            cfg: A MetaManager
            qudit: 'Q2', 'Q3', 'R4', 'R1'
            subspace: '01', '12'. Only work for qubit, not resonator.
            amp: Float number between 0 and 1.
            waveform_length: Integer between [4,16384]. Don't change.
    """
    def __init__(self, 
                 cfg, 
                 qudit: str,
                 subspace: str = '01',
                 amp: float = 0.1,
                 waveform_length: int = 40):
        self.cfg = cfg
        self.qudit = qudit
        self.subspace = subspace
        self.amp = amp
        self.waveform_length = waveform_length
        self.tone = f'{qudit}/{subspace}' if qudit.startswith('Q') else qudit
        
        # There are pointer to actual object in qblox driver.
        self.module = cfg.DAC.module[self.qudit]
        self.sequencer = cfg.DAC.sequencer[self.tone]

        self.module_idx = self.module._slot_idx
        self.sequencer_idx = self.sequencer._seq_idx
        
        
    def run(self):
        """
        Make sequence and start sequencer.
        """
        self.make_sequence()
        self.sequencer.arm_sequencer()
        self.cfg.DAC.qblox.start_sequencer()
        
        
    def make_sequence(self):
        """
        Set up sequence and configure the instrument.
        
        Reference: 
        https://qblox-qblox-instruments.readthedocs-hosted.com/en/master/tutorials/cont_wave_mode.html
        """
        sequence = {'waveforms': {'waveform':{'data': np.ones(self.waveform_length, dtype=float).tolist(), 
                                              'index': 0}}, 
                    'weights': {}, 
                    'acquisitions': {}, 
                    'program': f"""
                        wait_sync   4

                        loop:       play    0,0,{self.waveform_length}
                                    jmp     @loop
                        """
                    }

        file_path = os.path.join(self.cfg.working_dir, 'Jsons', 'SSB_Calibration.json')
        with open(file_path, 'w', encoding='utf-8') as file:
            json.dump(sequence, file, indent=4)

        self.cfg.DAC.qblox.reset()
        self.sequencer.sequence(file_path)
        self.sequencer.sync_en(True)
        self.sequencer.mod_en_awg(True)
        self.sequencer.marker_ovr_en(True)   
        self.sequencer.marker_ovr_value(15)
        self.sequencer.gain_awg_path0(self.amp)
        self.sequencer.gain_awg_path1(self.amp)

        if self.qudit.startswith('Q'):
            # Because the name of attribute depends on which output port.
            self.out = self.cfg[f'variables.{self.qudit}/out']
            self.att = self.cfg[f'DAC.Module{self.module_idx}/out{self.out}_att']
            getattr(self.module, f'out{self.out}_lo_freq')(self.cfg[f'variables.{self.qudit}/qubit_LO'])
            getattr(self.module, f'out{self.out}_att')(self.att)
            getattr(self.sequencer, f'channel_map_path0_out{self.out * 2}_en')(True)
            getattr(self.sequencer, f'channel_map_path1_out{self.out * 2 + 1}_en')(True)
            self.sequencer.nco_freq(self.cfg[f'variables.{self.tone}/mod_freq'])

        elif self.qudit.startswith('R'):
            self.out = 0
            self.att = self.cfg[f'DAC.Module{self.module_idx}/out0_att']
            self.module.out0_in0_lo_freq(self.cfg[f'variables.{self.qudit}/resonator_LO'])
            self.module.out0_att(self.att)
            self.sequencer.channel_map_path0_out0_en(True)
            self.sequencer.channel_map_path1_out1_en(True)
            self.sequencer.nco_freq(self.cfg[f'variables.{self.qudit}/mod_freq'])
            self.sequencer.nco_prop_delay_comp_en(True)


    def stop(self, save_cfg: bool = True):
        """
        Stop sequencer and store all the current value into cfg if asked.
        """
        self.cfg.DAC.qblox.stop_sequencer()
        if save_cfg:
            offset0 = getattr(self.module, f'out{self.out}_offset_path0')()
            offset1 = getattr(self.module, f'out{self.out}_offset_path1')()
            gain_ratio = self.sequencer.mixer_corr_gain_ratio()
            phase_offset = self.sequencer.mixer_corr_phase_offset_degree()

            self.cfg[f'DAC.Module{self.module_idx}/out{self.out}_offset_path0'] = offset0
            self.cfg[f'DAC.Module{self.module_idx}/out{self.out}_offset_path1'] = offset1
            self.cfg[f'DAC.Module{self.module_idx}/Sequencer{self.sequencer_idx}/mixer_corr_gain_ratio'] = gain_ratio
            self.cfg[f'DAC.Module{self.module_idx}/Sequencer{self.sequencer_idx}/mixer_corr_phase_offset_degree'] = phase_offset
            self.cfg.save()
            
            
    def set_offset0(self, offset0):
        """
        offset0 should be float number between [-84, 73].
        """
        getattr(self.module, f'out{self.out}_offset_path0')(offset0)


    def set_offset1(self, offset1):
        """
        offset1 should be float number between [-84, 73].
        """
        getattr(self.module, f'out{self.out}_offset_path1')(offset1)


    def set_gain_ratio(self, gain_ratio):
        """
        gain_ratio should be float number between [0.5, 2]. It usually takes [0.9, 1.1].
        """
        self.sequencer.mixer_corr_gain_ratio(gain_ratio)
        self.sequencer.arm_sequencer()
        self.cfg.DAC.qblox.start_sequencer()


    def set_phase_offset(self, phase_offset):
        """
        phase_offset should be float number between [-45, 45].
        """
        self.sequencer.mixer_corr_phase_offset_degree(phase_offset)
        self.sequencer.arm_sequencer()
        self.cfg.DAC.qblox.start_sequencer()