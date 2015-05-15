"""
Export neuroimaging results created with feat in FSL following NIDM-Results
specification.

@author: Camille Maumet <c.m.j.maumet@warwick.ac.uk>
@copyright: University of Warwick 2013-2014
"""

import re
import os
import sys
import glob
import json
import numpy as np
import subprocess
import warnings

# If "nidmresults" code is available locally work on the source code (used
# only for development)
FSL_EXPORTER_DIR = os.path.dirname(os.path.realpath(__file__))
NIDM_FSL_DIR = os.path.dirname(FSL_EXPORTER_DIR)
NIDM_RESULTS_FSL_DIR = os.path.dirname(NIDM_FSL_DIR)
NIDM_RESULTS_SRC_DIR = os.path.join(
    os.path.dirname(NIDM_RESULTS_FSL_DIR), "nidmresults")
if os.path.isdir(NIDM_RESULTS_SRC_DIR):
    sys.path.append(NIDM_RESULTS_SRC_DIR)

from nidmresults.exporter import NIDMExporter
from nidmresults.objects.constants import *
from nidmresults.objects.modelfitting import *
from nidmresults.objects.contrast import *
from nidmresults.objects.inference import *
from nidmfsl.fsl_exporter.objects.fsl_objects import *


class FSLtoNIDMExporter(NIDMExporter, object):

    """
    Parse an FSL result directory to extract the pieces information to be
    stored in NIDM-Results and generate a NIDM-Results export.
    """

    def __init__(self, *args, **kwargs):
        super(FSLtoNIDMExporter, self).__init__()

        self.feat_dir = kwargs.pop('feat_dir')

        nidm_dirs = glob.glob(os.path.join(self.feat_dir, 'nidm****'))
        if nidm_dirs:
            if nidm_dirs[-1] == os.path.join(self.feat_dir, 'nidm'):
                export_dir_num = 1
            else:
                m = re.search('(?<=nidm_).*', nidm_dirs[-1])
                export_dir_num = int(m.group(0)) + 1

            self.export_dir = os.path.join(
                self.feat_dir, 'nidm' + "_{0:0>4}".format(export_dir_num))
        else:
            self.export_dir = os.path.join(self.feat_dir, 'nidm')

        self.design_file = os.path.join(self.feat_dir, 'design.fsf')
        # FIXME: maybe not always "4"?
        feat_post_log_file = os.path.join(self.feat_dir, 'logs', 'feat4_post')
        # FIXME: this file is sometimes missing, can the connectivity info
        # be retreive from somewhere else??
        if os.path.isfile(feat_post_log_file):
            self.feat_post_log = open(feat_post_log_file, 'r')
        else:
            self.feat_post_log = None

        self.version = kwargs.pop('version')
        self.coord_space = None
        self.contrast_names_by_num = dict()

    def parse(self):
        """
        Parse an FSL result directory to extract the pieces information to be
        stored in NIDM-Results.
        """
        # Load design.fsf file
        design_file_open = open(self.design_file, 'r')
        self.design_txt = design_file_open.read()

        # Load feat post log file
        if self.feat_post_log is not None:
            self.feat_post_log = self.feat_post_log.read()

        fmri_level_re = r'.*set fmri\(level\) (?P<info>\d+).*'
        fmri_level = int(self._search_in_fsf(fmri_level_re))
        self.first_level = (fmri_level == 1)

        # FIXME cope1
        if self.first_level:
            # stat_dir = list([os.path.join(self.feat_dir, 'stats')])
            self.analysis_dirs = list([self.feat_dir])
        else:
            self.analysis_dirs = glob.glob(
                os.path.join(self.feat_dir, 'cope*.feat'))
            # cope_dirs
            # print cope_dirs
            # stat_dir = os.path.join(self.feat_dir, 'cope1.feat', 'stats')
            # analysis_dir = os.path.join(self.feat_dir, 'cope1.feat')

        super(FSLtoNIDMExporter, self).parse()

    def _add_namespaces(self):
        """
        Overload of parent _add_namespaces to add FSL namespace.
        """
        super(FSLtoNIDMExporter, self)._add_namespaces()
        self.doc.add_namespace(FSL)

    def _find_software(self):
        """
        Return an object of type Software describing the version of FSL used to
        compute the current analysis.
        """
        version_re = r'.*set fmri\(version\) (?P<info>\d+\.?\d+).*'
        feat_version = self._search_in_fsf(version_re)

        software = Software(feat_version=feat_version)

        return software

    def _find_model_fitting(self):
        """
        Parse FSL result directory to retreive model fitting information.
        Return a list of objects of type ModelFitting.
        """
        self.model_fittings = dict()

        for analysis_dir in self.analysis_dirs:
            stat_dir = os.path.join(analysis_dir, 'stats')

            design_matrix = self._get_design_matrix(analysis_dir)
            data = self._get_data()
            error_model = self._get_error_model()

            rms_map = self._get_residual_mean_squares_map(stat_dir)
            param_estimates = self._get_param_estimate_maps(stat_dir)
            mask_map = self._get_mask_map(analysis_dir)
            grand_mean_map = self._get_grand_mean(mask_map.file, analysis_dir)

            activity = self._get_model_parameters_estimations(error_model)

            model_fitting = ModelFitting(
                activity, design_matrix, data,
                error_model, param_estimates, rms_map, mask_map,
                grand_mean_map)

            self.model_fittings[analysis_dir] = model_fitting

        return self.model_fittings

    def _find_contrasts(self):
        """
        Parse FSL result directory to retreive information about contrasts.
        Return a dictionary of (key, value) pairs where key is a tuple
        containing the identifier of a ModelParametersEstimation object and a
        tuple of identifiers of ParameterEstimateMap objects, and value is an
        object of type Contrast.
        """
        for analysis_dir in self.analysis_dirs:
            # Retreive the Model Parameters Estimations activity corresponding
            # to current analysis directory.
            mf_id = self.model_fittings[analysis_dir].activity.id
            stat_dir = os.path.join(analysis_dir, 'stats')

            # Degrees of freedom
            # FIXME: check what happens when more than one contrast is
            # performed
            dof_file = open(os.path.join(stat_dir, 'dof'), 'r')
            dof = float(dof_file.read())

            for analysis_dir in self.analysis_dirs:
                exc_sets = glob.glob(os.path.join(analysis_dir,
                                                  'thresh_z*.nii.gz'))

                contrasts = dict()
                for filename in exc_sets:
                    s = re.compile('zf?stat\d+')
                    zstatnum = s.search(filename)
                    zstatnum = zstatnum.group()

                    if zstatnum.startswith("zstat"):
                        stat_type = "T"
                        con_num = zstatnum.replace('zstat', '')
                    elif zstatnum.startswith("zfstat"):
                        stat_type = "F"
                        con_num = zstatnum.replace('zfstat', '')

                    # If more than one excursion set is reported, we need to
                    # use an index in the file names of the file exported in
                    # nidm
                    if len(exc_sets) > 1:
                        stat_num = "_" + \
                            stat_type.upper() + "{0:0>3}".format(con_num)
                    else:
                        stat_num = ""

                    # Contrast name
                    name_re = r'.*set fmri\(conname_real\.' + con_num +\
                        '\) "(?P<info>[\w\s><]+)".*'
                    contrast_name = self._search_in_fsf(name_re)
                    self.contrast_names_by_num[con_num] = contrast_name

                    # Contrast estimation activity
                    estimation = ContrastEstimation(con_num, contrast_name)

                    # Contrast weights
                    weights_re = r'.*set fmri\(con_real' + con_num +\
                        '\.\d+\) (?P<info>-?\d+)'
                    weight_search = re.compile(weights_re)
                    contrast_weights = str(
                        re.findall(weight_search,
                                   self.design_txt)).replace("'", '')

                    weights = ContrastWeights(stat_num, contrast_name,
                                              contrast_weights, stat_type)

                    # Find which parameter estimates were used to compute the
                    # contrast
                    pe_ids = list()
                    pe_index = 1
                    contrast_weights = contrast_weights.replace(' ', '')
                    contrast_weights = contrast_weights.replace('[', '')
                    contrast_weights = contrast_weights.replace(']', '')
                    contrast_weights = contrast_weights.split(',')

                    # Whenever a "1" is found in contrast_weights, the
                    # parameter estimate map identified by the corresponding
                    # index is in use
                    for beta_index in contrast_weights:
                        if int(beta_index) == 1:
                            for model_fitting in self.model_fittings.values():
                                for pe in model_fitting.param_estimates:
                                    s = re.compile('pe\d+')
                                    pe_num = s.search(pe.file)
                                    pe_num = pe_num.group()
                                    pe_num = pe_num.replace('pe', '')
                                    if pe_num == pe_index:
                                        pe_ids.append(pe.id)
                        pe_index += 1

                    # Convert to immutable tuple to be used as key
                    pe_ids = tuple(pe_ids)

                    # Contrast Map
                    con_file = os.path.join(stat_dir,
                                            'cope' + str(con_num) + '.nii.gz')
                    contrast_map = ContrastMap(con_file, stat_num,
                                               contrast_name, self.coord_space,
                                               self.export_dir)

                    # Contrast Variance and Standard Error Maps
                    varcontrast_file = os.path.join(
                        stat_dir,
                        'varcope' + str(con_num) + '.nii.gz')
                    is_variance = True
                    std_err_map = ContrastStdErrMap(
                        stat_num,
                        varcontrast_file, is_variance, self.coord_space,
                        self.coord_space, self.export_dir)

                    # Statistic Map
                    stat_file = os.path.join(
                        stat_dir,
                        stat_type.lower() + 'stat' + str(con_num) + '.nii.gz')
                    stat_map = StatisticMap(
                        stat_file, stat_type, stat_num,
                        contrast_name, dof, self.coord_space,
                        self.export_dir)

                    # Z-Statistic Map
                    z_stat_file = os.path.join(
                        stat_dir,
                        'zstat' + str(con_num) + '.nii.gz')
                    z_stat_map = StatisticMap(
                        z_stat_file, 'Z', stat_num,
                        contrast_name, dof, self.coord_space,
                        self.export_dir)

                    con = Contrast(
                        con_num, contrast_name, weights, estimation,
                        contrast_map, std_err_map, stat_map, z_stat_map)

                    contrasts.setdefault((mf_id, pe_ids), list()).append(con)

        return contrasts

    def _find_inferences(self):
        """
        Parse FSL result directory to retreive information about inference
        along with peaks and clusters. Return a dictionary of (key, value)
        pairs where key is the identifier of a ContrastEstimation object and
        value is an object of type Inference.
        """
        inferences = dict()

        for analysis_dir in self.analysis_dirs:
            exc_sets = glob.glob(os.path.join(analysis_dir,
                                              'thresh_z*.nii.gz'))

            # Find excursion sets (in a given feat directory we have one
            # excursion set per contrast)
            for filename in exc_sets:
                s = re.compile('zf?stat\d+')
                zstatnum = s.search(filename)
                zstatnum = zstatnum.group()
                if zstatnum.startswith("zstat"):
                    stat_type = "T"
                    stat_num = zstatnum.replace('zstat', '')
                elif zstatnum.startswith("zfstat"):
                    stat_type = "F"
                    stat_num = zstatnum.replace('zfstat', '')

                # If more than one excursion set is reported, we need to use
                # an index in the file names of the file exported in nidm
                if len(exc_sets) > 1:
                    stat_num_t = "_" + \
                        stat_type.upper() + "{0:0>3}".format(stat_num)
                else:
                    stat_num_t = ""

                # Find corresponding contrast estimation activity
                con_id = None
                for contrasts in self.contrasts.values():
                    for contrast in contrasts:
                        s = re.compile('cope\d+\.nii\.gz')
                        con_num = s.search(contrast.contrast_map.file)
                        con_num = con_num.group()
                        con_num = con_num.replace('cope', '')\
                                         .replace('.nii.gz', '')
                        if con_num == stat_num:
                            con_id = contrast.estimation.id
                assert con_id is not None

                # Inference activity
                inference_act = InferenceActivity(
                    stat_num,
                    self.contrast_names_by_num[stat_num])

                # Excursion set
                visualisation = os.path.join(
                    analysis_dir,
                    'rendered_thresh_zstat' + stat_num + '.png')
                zFileImg = os.path.join(analysis_dir,
                                        'thresh_zstat' + stat_num + '.nii.gz')
                exc_set = ExcursionSet(zFileImg, stat_num_t, visualisation,
                                       self.coord_space, self.export_dir)

                # Height Threshold
                prob_re = r'.*set fmri\(prob_thresh\) (?P<info>\d+\.?\d+).*'
                z_re = r'.*set fmri\(z_thresh\) (?P<info>\d+\.?\d+).*'
                type_re = r'.*set fmri\(thresh\) (?P<info>\d+).*'

                prob_thresh = float(self._search_in_fsf(prob_re))
                z_thresh = float(self._search_in_fsf(z_re))
                thresh_type = int(self._search_in_fsf(type_re))

                # FIXME: deal with 0 = no thresh?
                voxel_uncorr = (thresh_type == 1)
                voxel_corr = (thresh_type == 2)
                cluster_thresh = (thresh_type == 3)

                stat_threshold = None
                extent_p_corr = None
                p_corr_threshold = None
                p_uncorr_threshold = None
                if voxel_uncorr:
                    p_uncorr_threshold = prob_thresh
                elif voxel_corr:
                    p_corr_threshold = prob_thresh
                else:
                    stat_threshold = z_thresh
                    extent_p_corr = prob_thresh

                height_thresh = HeightThreshold(
                    stat_threshold,
                    p_corr_threshold, p_uncorr_threshold)

                # Extent Threshold
                extent_thresh = ExtentThreshold(p_corr=extent_p_corr)

                # There is not table display listing peaks and clusters for
                # voxelwise correction
                if cluster_thresh:
                    # Clusters (and associated peaks)
                    clusters = self._get_clusters_peaks(stat_num)

                    # Peak and Cluster Definition Criteria
                    peak_criteria = PeakCriteria(
                        stat_num,
                        self._get_num_peaks(), self._get_peak_dist())
                    clus_criteria = ClusterCriteria(
                        stat_num,
                        self._get_connectivity())
                else:
                    clusters = None
                    peak_criteria = None
                    clus_criteria = None

                # FIXME: for now only based on conmask1_1
                m = re.search(
                    r"set fmri\(conmask1_1\) (?P<con_maskg>\d+)",
                    self.design_txt)
                assert m is not None
                contrast_masking = bool(int(m.group("con_maskg")))

                if contrast_masking:
                    # Display mask
                    # FIXME deal with the case in which we are contrast masking by
                    #  more than one contrast
                    # contrast_masking_search = re.compile(r'.*set
                    # fmri\(conmask'+contrast_num+'_(?P<maskingconnum>\d+)\)
                    # (?P<domask>\d+).*')
                    # contrast_masking_found =
                    # contrast_masking_search.search(self.design_txt)
                    # do_contrast_masking =
                    # float(contrast_masking_found.group('domask'))
                    # if do_contrast_masking:
                    #     contrast_masking_num =
                    # contrast_masking_found.group('maskingconnum')
                    #     contrast_masking_file =
                    # else:
                    #     contrast_masking_num = None
                    # FIXME: We need an example with more than one contrast to code
                    # contrast masking
                    contrast_masking_file = self._get_display_mask()
                    display_mask = DisplayMaskMap(
                        stat_num,
                        contrast_masking_file, self.coord_space,
                        self.export_dir)
                else:
                    display_mask = None

                # Search space
                search_space = self._get_search_space(analysis_dir)

                inference = Inference(
                    inference_act, height_thresh,
                    extent_thresh, peak_criteria, clus_criteria,
                    display_mask, exc_set, clusters, search_space,
                    self.software.id)

                inferences.setdefault(con_id, list()).append(inference)

        return inferences

    def _get_design_matrix(self, analysis_dir):
        """
        Parse FSL result directory to retreive information about the design
        matrix. Return an object of type DesignMatrix.
        """
        design_mat_file = os.path.join(analysis_dir, 'design.mat')
        design_mat_fid = open(design_mat_file, 'r')
        design_mat_values = np.loadtxt(design_mat_fid, skiprows=5, ndmin=2)
        design_mat_image = os.path.join(analysis_dir, 'design.png')

        # Regressor names (not taking into account HRF model)
        regnames_re = r'.*set fmri\(evtitle\d+\).*'
        ev_names = re.findall(regnames_re, self.design_txt)

        orig_ev = dict()
        for ev_name in ev_names:
            regname_re = r'.*set fmri\(evtitle(?P<num>\d+)\)\s*"(?P<name>.*)"'
            info_search = re.compile(regname_re)
            info_found = info_search.search(ev_name)
            num = info_found.group('num')
            name = info_found.group('name')
            orig_ev[int(num)] = name

        # For first-level fMRI only
        if self.first_level:
            # Design-type: event, mixed or block
            # FIXME: deal with other options than "custom"
            onsets_re = r'.*set fmri\(custom(?P<num>\d+)\)\s*"(?P<file>.*)".*'
            r = re.compile(onsets_re)
            onsets = [m.groupdict() for m in r.finditer(self.design_txt)]
            max_duration = 0
            min_duration = 36000
            for onset in onsets:
                aa = np.loadtxt(onset['file'])
                max_duration = max(max_duration, np.amax(aa[:, 2], axis=None))
                min_duration = min(min_duration, np.amin(aa[:, 2], axis=None))

            if max_duration <= 1:
                design_type = NIDM_EVENT_RELATED_DESIGN
            elif min_duration > 1:
                design_type = NIDM_BLOCK_BASED_DESIGN
            else:
                design_type = NIDM_MIXED_DESIGN

            # HRF model (only look at first ev)
            m = re.search(
                r"set fmri\(convolve1\) (?P<hrf>\d)", self.design_txt)
            assert m is not None
            hrf = int(m.group("hrf"))
            if hrf == 1:    # 1: Gaussian
                hrf_model = NIDM_GAUSSIAN_HRF
            elif hrf == 2:  # 2 : Gamma
                hrf_model = NIDM_GAMMA_HRF
            elif hrf == 3:  # 3 : Double-Gamma HRF
                hrf_model = FSL_FSLS_GAMMA_DIFFERENCE_HRF
            elif hrf == 4:  # 4 : Gamma basis functions
                hrf_model = NIDM_GAMMA_HRB
            elif hrf == 5:  # 5 : Sine basis functions
                hrf_model = NIDM_SINE_BASIS_SET
            elif hrf == 6:  # 6 : FIR basis functions
                hrf_model = NIDM_FINITE_IMPULSE_RESPONSE_HRB

            # Drift model
            m = re.search(
                r"set fmri\(paradigm_hp\) (?P<cut_off>\d+)", self.design_txt)
            assert m is not None
            cut_off = float(m.group("cut_off"))

            drift_model = DriftModel(
                FSL_GAUSSIAN_RUNNING_LINE_DRIFT_MODEL, cut_off)

        else:
            design_type = None
            hrf_model = None
            drift_model = None

        real_ev = list()
        for ev_num, ev_name in orig_ev.items():
            real_ev.append(ev_name)

            # Add one regressor name if there is an extra column for a temporal
            # derivative
            tempo_deriv_re = \
                r'.*set fmri\(deriv_yn'+str(ev_num)+'\) (?P<info>[\d]+).*'
            tempo_deriv = bool(self._search_in_fsf(tempo_deriv_re))

            if tempo_deriv:
                real_ev.append(ev_name+'*temporal_derivative')

            # FIXME: other hrf models (FIR...)

        design_matrix = DesignMatrix(design_mat_values, design_mat_image,
                                     self.export_dir, real_ev, design_type,
                                     hrf_model, drift_model)
        return design_matrix

    def _get_data(self):
        """
        Parse FSL result directory to retreive information about the data.
        Return an object of type Data.
        """
        grand_mean_scaling = True
        target_intensity = 10000.0
        data = Data(grand_mean_scaling, target_intensity)
        return data

    def _get_error_model(self):
        """
        Parse FSL result directory to retreive information about the error
        model. Return an object of type ErrorModel.
        """

        if self.first_level:
            variance_homo = True
            dependance = SERIALLY_CORR
            variance_spatial = SPATIALLY_LOCAL
            dependance_spatial = SPATIALLY_REGUL
        else:
            variance_homo = False
            dependance = INDEPEDENT_CORR
            variance_spatial = SPATIALLY_LOCAL
            dependance_spatial = None

        error_distribution = NIDM_GAUSSIAN_DISTRIBUTION
        error_model = ErrorModel(
            error_distribution, variance_homo,
            variance_spatial, dependance, dependance_spatial)
        return error_model

    def _get_residual_mean_squares_map(self, stat_dir):
        """
        Parse FSL result directory to retreive information about the residual
        mean squares map. Return an object of type ResidualMeanSquares.
        """
        if self.first_level:
            residuals_file = os.path.join(stat_dir, 'sigmasquareds.nii.gz')
        else:
            # FIXME cope num enter here
            sigma2_group_file = os.path.join(stat_dir,
                                             'mean_random_effects_var1.nii.gz')
            sigma2_sub_file = os.path.join(stat_dir,
                                           'varcope1.nii.gz')
            # Create residual mean squares map
            sigma2_group_img = nib.load(sigma2_group_file)
            sigma2_group = sigma2_group_img.get_data()

            sigma2_sub_img = nib.load(sigma2_sub_file)
            sigma2_sub = sigma2_sub_img.get_data()

            residuals_file = os.path.join(stat_dir,
                                          'calculated_sigmasquareds.nii.gz')
            residuals_img = nib.Nifti1Image(sigma2_group + sigma2_sub,
                                            sigma2_sub_img.get_qform())
            nib.save(residuals_img, residuals_file)

        # In FSL all files will be in the same coordinate space
        self.coord_space = CoordinateSpace(self._get_coordinate_system(),
                                           residuals_file)

        rms_map = ResidualMeanSquares(self.export_dir, residuals_file,
                                      self.coord_space)

        # FIXME: does not work
        # if not self.first_level:
        # Delete calculated rms file (a copy is now in the NIDM export)
        # FIXME we need to add the wasDerivedFrom maps
        #     os.remove(residuals_file)

        return rms_map

    def _get_param_estimate_maps(self, stat_dir):
        """
        Parse FSL result directory to retreive information about the parameter
        estimates. Return a list of objects of type ParameterEstimateMap.
        """
        param_estimates = list()

        for filename in os.listdir(stat_dir):
            if filename.startswith("pe"):
                if filename.endswith(".nii.gz"):
                    s = re.compile('pe\d+')
                    penum = s.search(filename)
                    penum = penum.group()
                    penum = penum.replace('pe', '')
                    full_path_file = os.path.join(stat_dir, filename)
                    param_estimate = ParameterEstimateMap(
                        full_path_file,
                        penum, self.coord_space)
                    param_estimates.append(param_estimate)
        return param_estimates

    def _get_mask_map(self, analysis_dir):
        """
        Parse FSL result directory to retreive information about the mask
        created as part of Model Parameters Estimation. Return an object of
        type MaskMap.
        """
        mask_file = os.path.join(analysis_dir, 'mask.nii.gz')
        mask_map = MaskMap(self.export_dir, mask_file,
                           self.coord_space, False)
        return mask_map

    def _get_grand_mean(self, mask_file, analysis_dir):
        """
        Parse FSL result directory to retreive information about the grand
        mean map. Return an object of type GrandMeanMap.
        """
        grand_mean_file = os.path.join(analysis_dir, 'mean_func.nii.gz')

        # FIXME: Check if there is an alternative file to use here (maybe)
        # depending on FSL version
        if not os.path.isfile(grand_mean_file):
            grand_mean = None
        else:
            grand_mean = GrandMeanMap(grand_mean_file, mask_file,
                                      self.coord_space, self.export_dir)

        return grand_mean

    def _get_coordinate_system(self):
        """
        Parse FSL result directory to retreive information about the
        coordinate system used in the current analysis (dependent on the
        template).
        """
        space_re = r'.*set fmri\(regstandard_yn\) (?P<info>[\d]+).*'
        standard_space = bool(self._search_in_fsf(space_re))

        if standard_space:
            custom_re = \
                r'.*set fmri\(alternateReference_yn\) (?P<info>[\d]+).*'
            custom_space = self._search_in_fsf(custom_re, True)
            if custom_space is not None:
                custom_space = (custom_space == "1")
            else:
                custom_space = False

            if custom_space is not None:
                custom_standard = (custom_space == "1")
            else:
                custom_re = r'.*set fmri\(regstandard\) (?P<info>.+).*'
                custom_space = self._search_in_fsf(custom_re)

                if custom_space is not None:
                    custom_standard = True
        else:
            custom_standard = False

        # TODO check if first level is always performed in subject space?
        if not standard_space or self.first_level:
            coordinate_system = NIDM_SUBJECT_COORDINATE_SYSTEM
        else:
            if not custom_standard:
                coordinate_system = \
                    NIDM_ICBM_MNI152_NON_LINEAR6TH_GENERATION_COORDINATE_SYSTEM
            else:
                coordinate_system = NIDM_STANDARDIZED_COORDINATE_SYSTEM

        return coordinate_system

    def _search_in_fsf(self, regexp, return_not_found=False):
        """
        Look for information matching regular expression 'regexp' in the design
        file of the current study.
        """
        info_search = re.compile(regexp)
        info_found = info_search.search(self.design_txt)
        if not info_found and return_not_found:
            info = None
        else:
            info = info_found.group('info')
        return info

    def _get_display_mask(self):
        """
        Parse FSL result directory to retreive information about display mask.
        """
        # FIXME this should be updated with actual contrast masking file
        mask_file = os.path.join(self.feat_dir, 'mask.nii.gz')
        return mask_file

    def _get_num_peaks(self):
        if self.feat_post_log is not None:
            num_peak_search = re.compile(r'.* --num=(?P<numpeak>\d+)+ .*')
            num_peak_found = num_peak_search.search(self.feat_post_log)
            if num_peak_found:
                num_peak = int(num_peak_found.group('numpeak'))
            else:
                num_peak_search = re.compile(r'.* -n=(?P<numpeak>\d+)+ .*')
                num_peak_found = num_peak_search.search(self.feat_post_log)
                if num_peak_found:
                    num_peak = int(num_peak_found.group('numpeak'))
                else:
                    # If not specified, default value is inf?
                    # (cf. http://fsl.fmrib.ox.ac.uk/fsl/fslwiki/Cluster)
                    # Is it ok to say no limit with -1 (as for Inf
                    # we would need float...)
                    # FIXME: for now omitted if not explicitely defined
                    num_peak = None
        else:
            num_peak = None
        return num_peak

    def _get_peak_dist(self):
        if self.feat_post_log is not None:
            peak_dist_search = re.compile(
                r'.* --peakdist=(?P<peakdist>\d+)+ .*')
            peak_dist_found = peak_dist_search.search(self.feat_post_log)
            if peak_dist_found:
                peak_dist = float(peak_dist_found.group('peakdist'))
            else:
                # If not specified, default value is zero (cf.
                # http://fsl.fmrib.ox.ac.uk/fsl/fslwiki/Cluster)
                peak_dist = 0.0
        else:
            peak_dist = 0.0

        return peak_dist

    def _get_connectivity(self):
        """
        Parse FSL result directory to retreive peak connectivity within a
        cluster.
        """
        if self.feat_post_log is not None:
            conn_re = r'.* --connectivity=(?P<connectivity>\d+)+ .*'
            connectivity_search = re.compile(conn_re)
            connectivity = int(
                connectivity_search.search(
                    self.feat_post_log).group('connectivity'))
        else:
            connectivity = None

        return connectivity

    def _get_search_space(self, analysis_dir):
        """
        Parse FSL result directory to retreive information about the search
        space. Return an object of type SearchSpace.
        """
        # FIXME this needs to be estimated
        search_space_file = os.path.join(analysis_dir, 'mask.nii.gz')

        smoothness_file = os.path.join(analysis_dir, 'stats', 'smoothness')

        # Load DLH, VOLUME, RESELS and noise FWHM
        with open(smoothness_file, "r") as fp:
            smoothness_txt = fp.read()

        sm_reg = \
            r"FWHMx = (?P<FWHMx_vx>\d+\.?\d*) voxels, " + \
            r"FWHMy = (?P<FWHMy_vx>\d+\.?\d*) voxels, " + \
            r"FWHMz = (?P<FWHMz_vx>\d+\.?\d*) voxels\n" + \
            r"FWHMx = (?P<FWHMx_mm>\d+\.?\d*) mm, " + \
            r"FWHMy = (?P<FWHMy_mm>\d+\.?\d*) mm, " + \
            r"FWHMz = (?P<FWHMz_mm>\d+\.?\d*) mm\n" + \
            r"DLH (?P<DLH>\d+\.?\d*) voxels\^\-3\n" + \
            r"VOLUME (?P<volume>\d+) voxels\n" + \
            r"RESELS (?P<vox_per_resels>\d+\.?\d*) voxels per resel"

        sm_match = re.search(sm_reg, smoothness_txt, re.DOTALL)

        if sm_match:
            d = sm_match.groupdict()
        else:
            # smoothness was estimated without the "-V" option, recompute
            log_file = os.path.join(self.feat_dir, 'logs', 'feat3_stats')

            if not os.path.isfile(log_file):
                warnings.warn("Log file feat3_stats not found, noise FWHM " +
                              "will not be reported")
                noise_fwhm_in_voxels = None
                noise_fwhm_in_units = None

                # Load DLH, VOLUME and RESELS
                d = dict()
                d['DLH'], d['volume'], d['vox_per_resels'] = \
                    np.loadtxt(smoothness_file, usecols=[1])
            else:
                with open(log_file, "r") as fp:
                    log_txt = fp.read()

                cmd_match = re.search(r"(?P<cmd>smoothest.*)\n", log_txt)
                cmd = cmd_match.group("cmd")
                cmd = cmd.replace("stats/smoothness", "stats/smoothness_v")
                cmd = cmd.replace("smoothest", "smoothest -V")
                subprocess.call("cd "+analysis_dir+";"+cmd, shell=True)

                with open(smoothness_file+"_v", "r") as fp:
                    smoothness_txt = fp.read()

                sm_match = re.search(sm_reg, smoothness_txt, re.DOTALL)
                d = sm_match.groupdict()

        vol_in_units = float(d['volume'])*np.prod(
            json.loads(self.coord_space.voxel_size))
        vol_in_resels = float(d['volume'])/float(d['vox_per_resels'])

        if 'FWHMx_vx' in d:
            noise_fwhm_in_voxels = json.dumps(
                [float(d['FWHMx_vx']), float(d['FWHMy_vx']),
                 float(d['FWHMz_vx'])])
            noise_fwhm_in_units = json.dumps(
                [float(d['FWHMx_mm']), float(d['FWHMy_mm']),
                 float(d['FWHMz_mm'])])

        search_space = SearchSpace(
            search_space_file=search_space_file,
            vol_in_voxels=int(d['volume']),
            vol_in_units=vol_in_units,
            vol_in_resels=vol_in_resels,
            resel_size_in_voxels=float(d['vox_per_resels']),
            dlh=float(d['DLH']),
            random_field_stationarity=True,
            noise_fwhm_in_voxels=noise_fwhm_in_voxels,
            noise_fwhm_in_units=noise_fwhm_in_units,
            coord_space=self.coord_space,
            export_dir=self.export_dir)

        return search_space

    def _get_clusters_peaks(self, stat_num):
        """
        Parse FSL result directory to retreive information about the clusters
        and peaks declared significant for statistic 'stat_num'. Return a list
        of Cluster objects.
        """
        clusters = list()

        for analysis_dir in self.analysis_dirs:
            # Cluster list (positions in voxels)
            cluster_file = os.path.join(analysis_dir,
                                        'cluster_zstat' + stat_num + '.txt')
            if not os.path.isfile(cluster_file):
                cluster_file = None
            else:
                cluster_table = np.loadtxt(cluster_file, skiprows=1, ndmin=2)

            # Cluster list (positions in mm)
            cluster_std_file = os.path.join(
                analysis_dir,
                'cluster_zstat' + stat_num + '_std.txt')
            if not os.path.isfile(cluster_std_file):
                cluster_std_file = None
                # cluster_std_table = np.zeros_like(cluster_table)*float('nan')
            else:
                cluster_std_table = np.loadtxt(cluster_std_file, skiprows=1,
                                               ndmin=2)

            # Peaks
            peak_file = os.path.join(
                analysis_dir, 'lmax_zstat' + stat_num + '.txt')
            if not os.path.isfile(peak_file):
                peak_file = None
            else:
                peak_table = np.loadtxt(peak_file, skiprows=1, ndmin=2)

            peak_std_file = os.path.join(analysis_dir,
                                         'lmax_zstat' + stat_num + '_std.txt')
            if not os.path.isfile(peak_std_file):
                peak_std_file = None
            else:
                peak_std_table = np.loadtxt(peak_std_file, skiprows=1, ndmin=2)

            peaks = dict()
            prev_cluster = -1
            if (peak_file is not None) and (peak_std_file is not None):

                peaks_join_table = np.column_stack(
                    (peak_table, peak_std_table))
                for peak_row in peaks_join_table:
                    cluster_id = int(peak_row[0])

                    if not cluster_id == prev_cluster:
                        # First peak in this cluster
                        peakIndex = 1

                    # Though peak coordinates in voxels are integer, we use a
                    # float type to comply with the rdfs:range
                    peak = Peak(
                        peak_index=int(peakIndex), x=int(peak_row[2]),
                        y=int(peak_row[3]), z=int(peak_row[4]),
                        x_std=peak_row[7], y_std=peak_row[8],
                        z_std=peak_row[9],
                        equiv_z=float(peak_row[1]),
                        cluster_index=cluster_id, stat_num=stat_num)
                    if cluster_id in peaks:
                        peaks[cluster_id].append(peak)
                    else:
                        peaks[cluster_id] = list([peak])

                    prev_cluster = cluster_id

                    peakIndex = peakIndex + 1
            elif (peak_file is not None):
                for peak_row in peak_table:
                    cluster_id = int(peak_row[0])

                    if not cluster_id == prev_cluster:
                        peakIndex = 1

                    peak = Peak(
                        peak_index=int(peakIndex), x=int(peak_row[2]),
                        y=int(peak_row[3]), z=int(peak_row[4]),
                        equiv_z=float(peak_row[1]), cluster_index=cluster_id,
                        stat_num=stat_num)
                    if cluster_id in peaks:
                        peaks[cluster_id].append(peak)
                    else:
                        peaks[cluster_id] = list([peak])

                    prev_cluster = cluster_id

                    peakIndex = peakIndex + 1
            elif (peak_std_file is not None):
                for peak_row in peak_std_table:
                    cluster_id = int(peak_row[0])

                    if not cluster_id == prev_cluster:
                        peakIndex = 1

                    peak = Peak(
                        peak_index=int(peakIndex),
                        x_std=peak_row[2],
                        y_std=peak_row[3],
                        z_std=peak_row[4],
                        equiv_z=float(peak_row[1]), cluster_index=cluster_id,
                        stat_num=stat_num)
                    if cluster_id in peaks:
                        peaks[cluster_id].append(peak)
                    else:
                        peaks[cluster_id] = list([peak])
                    prev_cluster = cluster_id

                    peakIndex = peakIndex + 1

            if (cluster_file is not None) and (cluster_std_file is not None):
                clusters_join_table = np.column_stack((cluster_table,
                                                       cluster_std_table))
                for cluster_row in clusters_join_table:
                    cluster_id = int(cluster_row[0])
                    size = int(cluster_row[1])
                    pFWER = float(cluster_row[2])
                    x = float(cluster_row[8])
                    y = float(cluster_row[9])
                    z = float(cluster_row[10])
                    x_std = float(cluster_row[24])
                    y_std = float(cluster_row[25])
                    z_std = float(cluster_row[26])
                    clusters.append(
                        Cluster(cluster_num=cluster_id, size=size,
                                pFWER=pFWER, peaks=peaks[
                                    cluster_id], x=x, y=y, z=z,
                                x_std=x_std, y_std=y_std, z_std=z_std))

            elif (cluster_file is not None):
                for cluster_row in cluster_table:
                    cluster_id = int(cluster_row[0])
                    size = int(cluster_row[1])
                    pFWER = float(cluster_row[2])
                    x = float(cluster_row[8])
                    y = float(cluster_row[9])
                    z = float(cluster_row[10])
                    x_std = None
                    y_std = None
                    z_std = None
                    clusters.append(
                        Cluster(cluster_num=cluster_id, size=size,
                                pFWER=pFWER, peaks=peaks[
                                    cluster_id], x=x, y=y, z=z,
                                x_std=x_std, y_std=y_std, z_std=z_std))
            elif (cluster_std_file is not None):
                for cluster_row in cluster_std_table:
                    cluster_id = int(cluster_row[0])
                    size = int(cluster_row[1])
                    pFWER = float(cluster_row[2])
                    x_std = float(cluster_row[8])
                    y_std = float(cluster_row[9])
                    z_std = float(cluster_row[10])
                    x = None
                    y = None
                    z = None
                    clusters.append(
                        Cluster(cluster_num=cluster_id, size=size,
                                pFWER=pFWER, peaks=peaks[
                                    cluster_id], x=x, y=y, z=z,
                                x_std=x_std, y_std=y_std, z_std=z_std))

        return clusters
