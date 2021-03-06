import os
import json

import numpy as np
import cv2
import torch
import smplx
from utils.common import json2kps, itt, to_tanh
from utils.bbox import crop_resize_coords, get_ltrb_bbox, crop_resize_verts, crop_resize_coords, crop_resize_image
from utils.smplx_models import build_smplx_model_dict
import pickle


class InferenceDataset():
    def __init__(self, samples_dir, image_size, v_inds, smplx_model_dir):
        self.samples_dir = samples_dir
        self.frame_list = self.list_samples(self.samples_dir)


        self.image_size = image_size
        self.input_size = image_size // 2


        self.v_inds = v_inds

        self.smplx_models_dict = build_smplx_model_dict(smplx_model_dir, device='cpu')

    @staticmethod
    def list_samples(samples_dir):
        files = os.listdir(samples_dir)
        frame_ids = [x.split('_')[0] for x in files]
        frame_ids = sorted(list(set(frame_ids)))

        return frame_ids

    def load_rgb(self, frame_id):
        rgb_path = os.path.join(self.samples_dir, f"{frame_id}_rgb.jpg")
        rgb = cv2.imread(rgb_path)[..., ::-1] / 255.
        return itt(rgb)

    def load_segm(self, frame_id):
        rgb_path = os.path.join(self.samples_dir, f"{frame_id}_segm.png")
        rgb = cv2.imread(rgb_path)[..., ::-1] / 255.
        return itt(rgb)

    def load_landmarks(self, frame_id):
        landmarks_path = os.path.join(self.samples_dir, f"{frame_id}_keypoints.json")
        with open(landmarks_path, 'r') as f:
            landmarks = json.load(f)
        landmarks = json2kps(landmarks)
        landmarks = torch.FloatTensor(landmarks).unsqueeze(0)
        return landmarks

    def load_smplx(self, frame_id):
        smplx_path = os.path.join(self.samples_dir, f"{frame_id}_smplx.pkl")
        with open(smplx_path, 'rb') as f:
            smpl_params = pickle.load(f)

        gender = smpl_params['gender']
        for k, v in smpl_params.items():
            if type(v) == np.ndarray:
                smpl_params[k] = torch.FloatTensor(v)

        smpl_params['left_hand_pose'] = smpl_params['left_hand_pose'][:, :6]
        smpl_params['right_hand_pose'] = smpl_params['right_hand_pose'][:, :6]

        with torch.no_grad():
            smpl_output = self.smplx_models_dict[gender](**smpl_params)
        vertices = smpl_output.vertices.detach()
        vertices = vertices[:, self.v_inds]
        K = smpl_params['camera_intrinsics'].unsqueeze(0)
        vertices = torch.bmm(vertices, K.transpose(1, 2))
        smpl_params.pop('camera_intrinsics')
        smpl_params['gender'] = [smpl_params['gender']]

        return vertices, K, smpl_params

    def __getitem__(self, item):
        frame_id = self.frame_list[item]

        rgb_orig = self.load_rgb(frame_id)
        segm_orig = self.load_segm(frame_id)
        landmarks_orig = self.load_landmarks(frame_id)
        verts_orig, K_orig, smpl_params = self.load_smplx(frame_id)

        ltrb = get_ltrb_bbox(verts_orig).float()
        vertices_crop, K_crop = crop_resize_verts(verts_orig, K_orig, ltrb, self.input_size)

        landmarks_crop = crop_resize_coords(landmarks_orig, ltrb, self.image_size)[0]
        rgb_crop = crop_resize_image(rgb_orig.unsqueeze(0), ltrb, self.image_size)[0]
        segm_crop = crop_resize_image(segm_orig.unsqueeze(0), ltrb, self.image_size)[0]

        rgb_crop = rgb_crop * segm_crop[:1]
        rgb_crop = to_tanh(rgb_crop)

        vertices_crop = vertices_crop[0]
        K_crop = K_crop[0]

        smpl_params = {k:v[0] for (k,v) in smpl_params.items()}
        data_dict = dict(real_rgb=rgb_crop, real_segm=segm_crop, landmarks=landmarks_crop, verts=vertices_crop,
                         K=K_crop)
        data_dict.update(smpl_params)

        return data_dict

    def __len__(self):
        return len(self.frame_list)
