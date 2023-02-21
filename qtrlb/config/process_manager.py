import numpy as np
from lmfit import Model
from qtrlb.config.config import Config
from qtrlb.config.variable_manager import VariableManager
from qtrlb.processing.processing import rotate_IQ, gmm_predict, normalize_population,\
                                        autorotate_IQ


class ProcessManager(Config):
    """ This is a thin wrapper over the Config class to help with measurement process management.
        The load() method will be called once in its __init__.
    
        Attributes:
            yamls_path: An absolute path of the directory containing all yamls with a template folder.
            variable_suffix: 'EJEC' or 'ALGO'. A underscroll will be added in this layer.
    """
    def __init__(self, 
                 yamls_path: str, 
                 varman: VariableManager):
        super().__init__(yamls_path=yamls_path, 
                         suffix='process',
                         varman=varman)
        self.load()
        
        
    def load(self):
        """
        Run the parent load, then check the shape of IQ matrices.
        """
        super().load()
        
        resonators_list = [key for key in self.keys() if key.startswith('R')]
        self.set('resonators', resonators_list, which='dict')  # Keep the new key start with lowercase!
        self.check_IQ_matrices()
        
        
    def check_IQ_matrices(self):
        """
        Check the shape of those IQ matrices inside process.yaml. 
        If their shapes are not compatible with readout_levels,
        default compatible matrices will be generated without saving.
        """
        for r in self['resonators']:
            self.set(f'{r}/n_readout_levels', len(self[f'{r}/readout_levels']), which='dict')
            
            try:
                assert (self[f'{r}/n_readout_levels'] == len(self[f'{r}/IQ_means']) 
                        == len(self[f'{r}/IQ_covariances']) == len(self[f'{r}/corr_matrix']))
            except AssertionError:
                print(f'ProcessManager: The shapes of IQ matrices in {r} are not compatible with its readout_levels. '
                      +'New matrices will be generated. Please save it by calling cfg.save()')
                
                self[f'{r}/corr_matrix'] = np.identity(self[f'{r}/n_readout_levels']).tolist()
                self[f'{r}/IQ_covariances'] = [1 for i in range(self[f'{r}/n_readout_levels'])]
                self[f'{r}/IQ_means'] = [[i,i] for i in range(self[f'{r}/n_readout_levels'])]
                
                
    def process_data(self, measurement: dict, fitmodel: Model):
        """
        Process the data by performing rotation, average, GMM, fit, plot, etc.
        Three common routine are hard coded here since we never change them.
        User can define new routine by adding new key in process.yaml and add code here.
        
        Note from Zihao(02/17/2023):
        The new key should better be the first 'if' condition below.
        Because one may need to keep heralding to be true to add that pulse in sequence,
        while going into the customized process routine.
        """

        if self['customized']:
            pass
        
        
        elif self['heralding']:
            # r is 'R3', 'R4', data_dict has key 'Heterodyned_readout', etc.
            for r, data_dict in self.measurement.items():  
                data_dict['IQrotated_readout'] = rotate_IQ(data_dict['Heterodyned_readout'], 
                                                           angle=self[f'{r}/IQ_rotation_angle'])
                data_dict['IQrotated_heralding'] = rotate_IQ(data_dict['Heterodyned_heralding'], 
                                                           angle=self[f'{r}/IQ_rotation_angle'])
                
                data_dict['GMMpredicted_readout'] = gmm_predict(data_dict['IQrotated_readout'], 
                                                                means=self[f'{r}/IQ_means'], 
                                                                covariances=self[f'{r}/IQ_covariances'])
                data_dict['GMMpredicted_heralding'] = gmm_predict(data_dict['IQrotated_heralding'], 
                                                                  means=self[f'{r}/IQ_means'], 
                                                                  covariances=self[f'{r}/IQ_covariances'])
                
            heralding_mask = self.heralding_test()
            
            for r, data_dict in self.measurement.items(): 
                data_dict['Mask_heralding'] = heralding_mask
                
                data_dict['PopulationNormalized_readout'] = normalize_population(data_dict['GMMpredicted_readout'],
                                                                                 n_levels=self[f'{r}/n_readout_levels'],
                                                                                 mask=data_dict['Mask_heralding'])
                
                data_dict['PopulationCorrected_readout'] = np.linalg.solve(self[f'{r}/corr_matrix'],
                                                                           data_dict['PopulationNormalized_readout'])
                
                data_dict['to_fit'] = data_dict['PopulationCorrected_readout']
            
            
        elif self['classification']:
            for r, data_dict in self.measurement.items():  
                data_dict['IQrotated_readout'] = rotate_IQ(data_dict['Heterodyned_readout'], 
                                                           angle=self[f'{r}/IQ_rotation_angle'])
                
                data_dict['GMMpredicted_readout'] = gmm_predict(data_dict['IQrotated_readout'], 
                                                                means=self[f'{r}/IQ_means'], 
                                                                covariances=self[f'{r}/IQ_covariances'])
                
                data_dict['PopulationNormalized_readout'] = normalize_population(data_dict['GMMpredicted_readout'],
                                                                                 n_levels=self[f'{r}/n_readout_levels'])
                
                data_dict['PopulationCorrected_readout'] = np.linalg.solve(self[f'{r}/corr_matrix'],
                                                                           data_dict['PopulationNormalized_readout'])
                
                data_dict['to_fit'] = data_dict['PopulationCorrected_readout']

            
        else:
            for r, data_dict in self.measurement.items():  
                data_dict['IQrotated_readout'] = rotate_IQ(data_dict['Heterodyned_readout'], 
                                                           angle=self[f'{r}/IQ_rotation_angle'])
    
                data_dict['IQautorotated_readout'] = autorotate_IQ(data_dict['IQrotated_readout'], 
                                                                   n_components=self[f'{r}/n_readout_levels'])
    
                data_dict['IQaveraged_readout'] = np.mean(data_dict['IQautorotated_readout'], axis=1)
        
                data_dict['to_fit'] = data_dict['IQaveraged_readout']
        

    def heralding_test(self):
        """
        Generate the ndarray heralding_mask with shape (n_reps, x_points).
        The entries will be 0 only if all resonators gives 0 in heralding.
        It means for that specific repetition and x_point, all resonators pass heralding test.
        We then truncate data to make sure all x_point has same amount of available repetition.
        
        Note from Zihao(02/21/2023):
        The code here is stolen from original version of qtrl where we can only test ground state.
        However, ground state has most population and if our experiment need to start from |1>, pi pulse it.
        The code here is ugly and hard to read, please make it better if you know how to do it.
        """
        resonators = self.measurement.keys()
        heralding_mask = np.zeros_like(self.measurement[resonators[0]]['GMMpredicted_heralding'])
        
        for r in resonators:
            heralding_mask = heralding_mask | self.measurement[r]['GMMpredicted_heralding']
            
        n_pass_min = np.min(np.sum(heralding_mask == 0, axis=0))  
        
        for i in range(heralding_mask.shape[1]): # Loop over each x_point
            j = 0
            while np.sum(heralding_mask[:, i] == 0) > n_pass_min:
                n_short = np.sum(heralding_mask[:, i] == 0) - n_pass_min
                heralding_mask[j : j + n_short, i] = -1
                j += n_short
                
        return heralding_mask 


    def fit_data(self): 
        # TODO: think about how we do fit. Need to consider 2D data and multiple qubit scan.
        # Maybe it really worth to have the fit and plot outside the process_data.
        # Especially we actually have multiple level to fit.
        # Think about which layer to put it will be better.
        # Think about the two level trick of fit.
        # data_dict['to_fit'] usually have shape (n_levels, x_points), or (2, x_points) for IQ.
        return
    