# deep learning
import torch
import torch.nn.functional as F
from torch.autograd import Variable
from torch.utils.data import DataLoader
from torchvision.utils import save_image

# general module
import numpy as np
import argparse
import yaml
from addict import Dict
import cv2
import numpy as np
import warnings
warnings.filterwarnings("ignore") 
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import glob
import sys
from PIL import Image
import os
import time
from tqdm import tqdm
import torch.nn as nn

print("===> Getting configuration")
parser = argparse.ArgumentParser(description="parameters for training")
parser.add_argument("config", type=str, help="configuration yaml file path")
args = parser.parse_args()
cfg_arg = Dict(yaml.safe_load(open(args.config)))
print(os.path.join(cfg_arg.exp_set.save_folder, cfg_arg.data.name, cfg_arg.exp_set.model_name, 'train*.yaml'))
saved_yaml_file_path = glob.glob(os.path.join(cfg_arg.exp_set.save_folder, cfg_arg.data.name, cfg_arg.exp_set.model_name, 'train*.yaml'))[0]
cfg = Dict(yaml.safe_load(open(saved_yaml_file_path)))
cfg.update(cfg_arg)
print(cfg)

print("===> Setting gpu numbers")
# update gpu number for roi_align
import os
os.environ['CUDA_VISIBLE_DEVICES'] = f'{cfg_arg.exp_set.gpu_start}'
cfg.exp_set.gpu_start, cfg.exp_set.gpu_finish = 0, 0
gpus_list = range(cfg.exp_set.gpu_start, cfg.exp_set.gpu_finish+1)
cuda = cfg.exp_set.gpu_mode

# original module
from dataset.dataset_selector import dataset_generator
from models.model_selector import model_generator

# generate data type
def data_type_id_generator(head_vector_gt, head_tensor, gt_box, cfg):
    data_type_id = ''

    if cfg.data.name == 'volleyball':
        data_type_id = f'bbox_{cfg.exp_params.bbox_types}_gaze_{cfg.exp_params.gaze_types}_act_{cfg.exp_params.action_types}'
    elif cfg.data.name == 'volleyball_wo_att':
        data_type_id = f'bbox_{cfg.exp_params.bbox_types}_gaze_{cfg.exp_params.gaze_types}_act_{cfg.exp_params.action_types}'
    elif cfg.data.name == 'videocoatt':
        dets_people_num = np.sum(np.sum(head_vector_gt, axis=-1) != 0)
        # define data id of dets people
        dets_people_num = np.sum(np.sum(head_vector_gt, axis=-1) != 0)
        if dets_people_num <= 3:
            dets_people_id = '0<peo<3'
        else:
            dets_people_id = '3<=peo'

        # define data id of gaze estimation
        head_vector_gt_cos = head_vector_gt[:dets_people_num, :]
        head_vector_pred_cos = head_tensor[:dets_people_num, :2]
        head_gt_pred_cos_sim = np.sum(head_vector_gt_cos * head_vector_pred_cos, axis=1)
        head_gt_pred_cos_sim_ave = np.sum(head_gt_pred_cos_sim) / dets_people_num
        if head_gt_pred_cos_sim_ave < 0.5:
            gaze_error_id = '0_0<gaze<0_5'
        else:
            gaze_error_id = '0_5_gaze<1_0'

        # define data id of joint attention size
        gt_x_min, gt_y_min, gt_x_max, gt_y_max = gt_box[0, :]
        gt_x_size, gt_y_size = gt_x_max-gt_x_min, gt_y_max-gt_y_min
        gt_x_size /= cfg.exp_set.resize_width
        gt_y_size /= cfg.exp_set.resize_height
        gt_size = ((gt_x_size**2)+(gt_y_size**2))**0.5
        if gt_size < 0.1:
            gt_size_id = '0_0<size<0_1'
        else:
            gt_size_id = '0_1<size'

        # data_type_id = f'{dets_people_id}:{gaze_error_id}:{gt_size_id}'
        # data_type_id = f'{dets_people_id}:{gaze_error_id}'
        data_type_id = ''

    return data_type_id

# generate data id
def data_id_generator(img_path, cfg):
    data_id = 'unknown'
    if cfg.data.name == 'volleyball':
        video_num, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{video_num}_{seq_num}_{img_num}'
    elif cfg.data.name == 'volleyball_wo_att':
        video_num, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{video_num}_{seq_num}_{img_num}'
    elif 'videocoatt' in cfg.data.name:
        mode, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{mode}_{seq_num}_{img_num}'
    elif cfg.data.name == 'videoattentiontarget':
        vid_name, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{vid_name}_{seq_num}_{img_num}'
    elif cfg.data.name == 'toy':
        vid_name, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{vid_name}_{seq_num}_{img_num}'
    elif cfg.data.name == 'gazefollow':
        mode, seq_num, img_name = img_path.split('/')[-3:]
        img_num = img_name.split('.')[0]
        data_id = f'{mode}_{seq_num}_{img_num}'

    return data_id

# normalize heatmap
def norm_heatmap(img_heatmap):
    if np.min(img_heatmap) == np.max(img_heatmap):
        img_heatmap[:, :] = 0
    else: 
        img_heatmap = (img_heatmap - np.min(img_heatmap)) / (np.max(img_heatmap) - np.min(img_heatmap))
        img_heatmap *= 255

    return img_heatmap

def action_idx_to_name(action_idx):
    ACTIONS = ['blocking', 'digging', 'falling', 'jumping',
                'moving', 'setting', 'spiking', 'standing',
                'waiting']

    return ACTIONS[action_idx]

def generate_video_from_frames(frames, video_path, fps=30):
    fourcc = cv2.VideoWriter_fourcc('m', 'p', '4', 'v')
    video = cv2.VideoWriter(video_path, fourcc, fps, (original_width, original_height))
    for frame in frames:
        video.write(frame)
    video.release()

print("===> Building model")
model_head, model_attention, model_saliency, model_fusion, cfg = model_generator(cfg)

print("===> Building seed configuration")
np.random.seed(cfg.exp_set.seed_num)
torch.manual_seed(cfg.exp_set.seed_num)
torch.backends.cudnn.benchmark=True
torch.backends.cudnn.deterministic=True
torch.use_deterministic_algorithms=True

print("===> Loading trained model")
model_name = cfg.exp_set.model_name
weight_saved_dir = os.path.join(cfg.exp_set.save_folder,cfg.data.name, model_name)
model_head_weight_path = os.path.join(weight_saved_dir, "model_head_best.pth.tar")
model_head.load_state_dict(torch.load(model_head_weight_path,  map_location='cuda:'+str(gpus_list[0])))

model_saliency_weight_path = os.path.join(weight_saved_dir, "model_saliency_best.pth.tar")
if os.path.exists(model_saliency_weight_path):
    model_saliency.load_state_dict(torch.load(model_saliency_weight_path,  map_location='cuda:'+str(gpus_list[0])))

model_attention_weight_path = os.path.join(weight_saved_dir, "model_gaussian_best.pth.tar")
model_attention.load_state_dict(torch.load(model_attention_weight_path,  map_location='cuda:'+str(gpus_list[0])))

model_fusion_weight_path = os.path.join(weight_saved_dir, "model_fusion_best.pth.tar")
if os.path.exists(model_fusion_weight_path):
    model_fusion.load_state_dict(torch.load(model_fusion_weight_path,  map_location='cuda:'+str(gpus_list[0])))
# model_fusion.load_state_dict(torch.load(model_fusion_weight_path,  map_location='cuda:'+str(gpus_list[0])))

if cuda:
    model_head = model_head.cuda(gpus_list[0])
    model_saliency = model_saliency.cuda(gpus_list[0])
    model_attention = model_attention.cuda(gpus_list[0])
    model_fusion = model_fusion.cuda(gpus_list[0])
    model_head.eval()
    model_saliency.eval()
    model_fusion.eval()

# view learned fusion coeficient
if 'ja_transformer' in cfg.model_params.model_type and not 'only_people' in cfg.model_params.model_type:
    fusion_weights = model_fusion.state_dict()['final_fusion_weight'].detach().cpu()
    m = nn.Softmax()
    fusion_weights = m(fusion_weights)
    print(f'(P_P:P_S)=({fusion_weights[0]:.2f}:{fusion_weights[1]:.2f})')

print("===> Loading dataset")
mode = cfg.exp_set.mode
# cfg.data.name = 'videocoatt_no_att'
test_set = dataset_generator(cfg, mode)
test_data_loader = DataLoader(dataset=test_set,
                                batch_size=cfg.exp_set.batch_size,
                                shuffle=False,
                                num_workers=cfg.exp_set.num_workers,
                                pin_memory=True)
print('{} demo samples found'.format(len(test_set)))

print("===> Making directories to save results")
if cfg.exp_set.test_gt_gaze:
    model_name = model_name + f'_use_gt_gaze'

save_image_dir_dic = {}
save_image_dir_list = ['person_person_att', 'person_person_jo_att',
                       'person_person_att_superimposed', 'person_person_jo_att_superimposed',
                       'person_person_jo_att_superimposed_video',
                       'person_person_jo_att_superimposed_traj',
                       'person_scene_att', 'person_scene_jo_att',
                       'person_scene_att_superimposed', 'person_scene_jo_att_superimposed',
                       'person_scene_jo_att_superimposed_video',
                       'person_scene_ang_att', 'person_scene_ang_att_superimposed',
                       'person_scene_ang_att_superimposed_video',
                       'final_jo_att', 'final_jo_att_superimposed',
                       'final_jo_att_superimposed_video',
                       'gt_map', 'person_person_self_att_weight',
                       'whole_image', 'whole_image_gaze', 'whole_image_action',
                       ]
for dir_name in save_image_dir_list:
    save_image_dir_dic[dir_name] = os.path.join('results', cfg.data.name, model_name, dir_name)
    if not os.path.exists(save_image_dir_dic[dir_name]):
        os.makedirs(save_image_dir_dic[dir_name])

print("===> Starting demo processing")
stop_iteration = 10
# if mode == 'test':
    # stop_iteration = 500
for iteration, batch in enumerate(test_data_loader,1):
    if iteration > stop_iteration:
        break

    # init heatmaps
    if len(batch['head_img'].shape) == 5:
        batch_size, num_people = batch['head_img'].shape[0:2]
    else:
        batch_size, frame_num, num_people = batch['head_img'].shape[0:3]
    x_axis_map = torch.arange(0, cfg.exp_set.resize_width, device=f'cuda:{gpus_list[0]}').reshape(1, -1)/(cfg.exp_set.resize_width)
    x_axis_map = torch.tile(x_axis_map, (cfg.exp_set.resize_height, 1))
    y_axis_map = torch.arange(0, cfg.exp_set.resize_height, device=f'cuda:{gpus_list[0]}').reshape(-1, 1)/(cfg.exp_set.resize_height)
    y_axis_map = torch.tile(y_axis_map, (1, cfg.exp_set.resize_width))
    xy_axis_map = torch.cat((x_axis_map[None, :, :], y_axis_map[None, :, :]))[None, None, :, :, :]
    xy_axis_map = torch.tile(xy_axis_map, (cfg.exp_set.batch_size, num_people, 1, 1, 1))
    head_x_map = torch.ones((cfg.exp_set.batch_size, num_people, 1, cfg.exp_set.resize_height, cfg.exp_set.resize_width), device=f'cuda:{gpus_list[0]}')
    head_y_map = torch.ones((cfg.exp_set.batch_size, num_people, 1, cfg.exp_set.resize_height, cfg.exp_set.resize_width), device=f'cuda:{gpus_list[0]}')
    head_xy_map = torch.cat((head_x_map, head_y_map), 2)
    gaze_x_map = torch.ones((cfg.exp_set.batch_size, num_people, 1, cfg.exp_set.resize_height, cfg.exp_set.resize_width), device=f'cuda:{gpus_list[0]}')
    gaze_y_map = torch.ones((cfg.exp_set.batch_size, num_people, 1, cfg.exp_set.resize_height, cfg.exp_set.resize_width), device=f'cuda:{gpus_list[0]}')
    gaze_xy_map = torch.cat((gaze_x_map, gaze_y_map), 2)
    xy_axis_map = xy_axis_map.float()
    head_xy_map = head_xy_map.float()
    gaze_xy_map = gaze_xy_map.float()
    batch['xy_axis_map'] = xy_axis_map
    batch['head_xy_map'] = head_xy_map
    batch['gaze_xy_map'] = gaze_xy_map

    with torch.no_grad():            
        # move data into gpu
        if cuda:
            for key, val in batch.items():
                if torch.is_tensor(val):
                    batch[key] = Variable(val).cuda(gpus_list[0])

        if cfg.model_params.use_position:
            input_feature = batch['head_feature'].clone() 
        else:
            input_feature = batch['head_feature'].clone()
            input_feature[:, :, :2] = input_feature[:, :, :2] * 0
        batch['input_feature'] = input_feature

        # head pose estimation
        out_head = model_head(batch)
        batch['head_img_extract'] = out_head['head_img_extract']

        if cfg.exp_params.gaze_types == 'GT':
            batch['head_vector'] = batch['head_vector_gt']
        else:
            batch['head_vector'] = out_head['head_vector']

        # change position inputs
        if cfg.model_params.use_gaze:
            batch['input_gaze'] = batch['head_vector'].clone() 
        else:
            batch['input_gaze'] = batch['head_vector'].clone() * 0

        # scene feature extraction
        out_scene_feat = model_saliency(batch)
        batch = {**batch, **out_scene_feat}

        # joint attention estimation
        out_attention = model_attention(batch)
        batch = {**batch, **out_attention}

        # fusion network
        out_fusion = model_fusion(batch)
        batch = {**batch, **out_fusion}

        # loss_set_head = model_head.calc_loss(batch, batch)
        loss_set_saliency = model_saliency.calc_loss(batch, batch, cfg)
        loss_set_attention = model_attention.calc_loss(batch, batch, cfg)

        out = {**out_head, **out_scene_feat, **out_attention, **batch}

    # img_gt_vid = out['img_gt'].to('cpu').detach()[0]
    # head_vector_vid = out['head_vector'].to('cpu').detach()[0].numpy()
    # head_vector_gt_vid = out['head_vector_gt'].to('cpu').detach()[0].numpy()
    # head_feature_vid = out['head_feature'].to('cpu').detach()[0]
    # head_bbox_vid = out['head_bbox'].to('cpu').detach()[0].numpy()
    # gt_box_vid = out['gt_box'].to('cpu').detach()[0]
    # att_inside_flag = out['att_inside_flag'].to('cpu').detach()[0]
    # person_person_attention_heatmap_vid = out['person_person_attention_heatmap'].to('cpu').detach()[0]
    # person_person_joint_attention_heatmap_vid = out['person_person_joint_attention_heatmap'].to('cpu').detach()[0]
    # person_scene_attention_heatmap_vid = out['person_scene_attention_heatmap'].to('cpu').detach()[0]
    # person_scene_joint_attention_heatmap_vid = out['person_scene_joint_attention_heatmap'].to('cpu').detach()[0]
    # final_joint_attention_heatmap_vid = out['final_joint_attention_heatmap'].to('cpu').detach()[0]

    img_gt_vid = out['img_gt'].to('cpu').detach()
    head_vector_vid = out['head_vector'].to('cpu').detach()
    head_vector_gt_vid = out['head_vector_gt'].to('cpu').detach()
    head_feature_vid = out['head_feature'].to('cpu').detach()
    head_bbox_vid = out['head_bbox'].to('cpu').detach()
    gt_box_vid = out['gt_box'].to('cpu').detach()
    att_inside_flag = out['att_inside_flag'].to('cpu').detach()
    person_person_attention_heatmap_vid = out['person_person_attention_heatmap'].to('cpu').detach()
    person_person_joint_attention_heatmap_vid = out['person_person_joint_attention_heatmap'].to('cpu').detach()
    person_scene_attention_heatmap_vid = out['person_scene_attention_heatmap'].to('cpu').detach()
    person_scene_joint_attention_heatmap_vid = out['person_scene_joint_attention_heatmap'].to('cpu').detach()
    final_joint_attention_heatmap_vid = out['final_joint_attention_heatmap'].to('cpu').detach()
    data_id_vid = out['data_id'][0]
    data_id = data_id_vid

    if cfg.model_params.p_s_estimator_type == 'cnn':
        ang_att_map = out['ang_att_map'].to('cpu').detach()[0]

    # define data id
    data_type_id = ''
    print(f'Iter:{iteration}, {data_id_vid}, {data_type_id}')
    resize_height = cfg.exp_set.resize_height
    resize_width = cfg.exp_set.resize_width

    pred_ja_vid = np.zeros((len(out['rgb_path']), 2))
    gt_ja_vid = np.zeros((len(out['rgb_path']), 2))

    for img_idx, img_path in enumerate(tqdm(out['rgb_path'])):
        person_person_joint_attention_heatmap = person_person_joint_attention_heatmap_vid[img_idx]
        person_scene_joint_attention_heatmap = person_scene_joint_attention_heatmap_vid[img_idx]
        final_joint_attention_heatmap = final_joint_attention_heatmap_vid[img_idx]
        img_gt = img_gt_vid[img_idx]
        head_vector = head_vector_vid[img_idx]
        head_vector_gt = head_vector_gt_vid[img_idx]
        head_feature = head_feature_vid[img_idx]
        head_bbox = head_bbox_vid[img_idx]
        gt_box = gt_box_vid[img_idx]
        person_person_attention_heatmap = person_person_attention_heatmap_vid[img_idx]
        person_scene_attention_heatmap = person_scene_attention_heatmap_vid[img_idx]

        # redefine image size
        # data_id = data_id_generator(img_path, cfg)
        img = cv2.imread(img_path)
        original_height, original_width, _ = img.shape
        cfg.exp_set.resize_height = original_height
        cfg.exp_set.resize_width = original_width

        # generate directories
        single_image_dir_list = ['person_person_jo_att_superimposed_video',
                                'person_scene_jo_att_superimposed_video',
                                'final_jo_att_superimposed_video',
                                ]
        for dir_name in single_image_dir_list:
            if not os.path.exists(os.path.join(save_image_dir_dic[dir_name], data_type_id)):
                os.makedirs(os.path.join(save_image_dir_dic[dir_name], data_type_id))
        
        multi_image_dir_list = ['gt_map', 'person_person_self_att_weight',
                                'person_person_att', 'person_person_att_superimposed',
                                'person_person_jo_att', 'person_person_jo_att_superimposed',
                                'person_person_jo_att_superimposed_traj',
                                'person_scene_att', 'person_scene_att_superimposed',
                                'person_scene_jo_att', 'person_scene_jo_att_superimposed',
                                'final_jo_att', 'final_jo_att_superimposed',
                                ]
        for dir_name in multi_image_dir_list:
            if not os.path.exists(os.path.join(save_image_dir_dic[dir_name], data_type_id, f'{data_id_vid}')):
                os.makedirs(os.path.join(save_image_dir_dic[dir_name], data_type_id, f'{data_id_vid}'))

        # save whole image
        cv2.imwrite(os.path.join(save_image_dir_dic['whole_image'], data_type_id, f'{mode}_{data_id}_whole_image.png'), img)

        # save joint attention estimation
        save_image(person_person_joint_attention_heatmap, os.path.join(save_image_dir_dic['person_person_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_person_jo_att.png'))
        save_image(person_scene_joint_attention_heatmap, os.path.join(save_image_dir_dic['person_scene_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_scene_jo_att.png'))
        save_image(final_joint_attention_heatmap, os.path.join(save_image_dir_dic['final_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_final_jo_att.png'))

        # save attention of each person
        key_no_padding_num = torch.sum((torch.sum(head_feature, dim=-1) != 0)).numpy()
        for person_idx in range(key_no_padding_num):
            save_image(img_gt[person_idx], os.path.join(save_image_dir_dic['gt_map'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_gt.png'))
            save_image(person_person_attention_heatmap[person_idx], os.path.join(save_image_dir_dic['person_person_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_person_att.png'))
            save_image(person_scene_attention_heatmap[person_idx], os.path.join(save_image_dir_dic['person_scene_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_scene_att.png'))
            # if cfg.model_params.p_s_estimator_type == 'cnn':
                # save_image(ang_att_map[person_idx], os.path.join(save_image_dir_dic['person_scene_ang_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_scene_ang_att.png'))

        # save attention of transformers (people and people attention)
        # if 'ja_transformer' in cfg.model_params.model_type:
        #     trans_att_people_people = trans_att_people_people_vid[img_idx]
        #     key_no_padding_num = torch.sum((torch.sum(head_feature, dim=-1) != 0)).numpy()+1
        #     df_person = [person_idx for person_idx in range(key_no_padding_num)]
        #     people_people_trans_enc_num = cfg.model_params.people_people_trans_enc_num
        #     for i in range(people_people_trans_enc_num):
        #         plt.figure(figsize=(8, 6))
        #         trans_att_people_people_enc = pd.DataFrame(data=trans_att_people_people[i, :key_no_padding_num, :key_no_padding_num], index=df_person, columns=df_person)
        #         sns.heatmap(trans_att_people_people_enc, cmap='jet')
        #         plt.savefig(os.path.join(save_image_dir_dic['person_person_self_att_weight'], data_type_id, data_id_vid, f'{mode}_{data_id}_enc{i}_person_person_self_att_weight.png'))
        #         plt.close()

        # save joint attention estimation as a superimposed image
        img = cv2.resize(img, (cfg.exp_set.resize_width, cfg.exp_set.resize_height))
        person_person_joint_attention_heatmap = cv2.imread(os.path.join(save_image_dir_dic['person_person_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_person_jo_att.png'), cv2.IMREAD_GRAYSCALE)
        person_scene_joint_attention_heatmap = cv2.imread(os.path.join(save_image_dir_dic['person_scene_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_scene_jo_att.png'), cv2.IMREAD_GRAYSCALE)
        final_joint_attention_heatmap = cv2.imread(os.path.join(save_image_dir_dic['final_jo_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_final_jo_att.png'), cv2.IMREAD_GRAYSCALE)
        person_person_joint_attention_heatmap = cv2.resize(person_person_joint_attention_heatmap, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        person_scene_joint_attention_heatmap = cv2.resize(person_scene_joint_attention_heatmap, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        final_joint_attention_heatmap = cv2.resize(final_joint_attention_heatmap, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
        person_person_joint_attention_heatmap = norm_heatmap(person_person_joint_attention_heatmap).astype(np.uint8)
        person_scene_joint_attention_heatmap = norm_heatmap(person_scene_joint_attention_heatmap).astype(np.uint8)
        final_joint_attention_heatmap = norm_heatmap(final_joint_attention_heatmap).astype(np.uint8)

        # get estimated joint attention coordinates
        pred_y_mid_p_p, pred_x_mid_p_p = np.unravel_index(np.argmax(person_person_joint_attention_heatmap), person_person_joint_attention_heatmap.shape)
        pred_y_mid_p_s, pred_x_mid_p_s = np.unravel_index(np.argmax(person_scene_joint_attention_heatmap), person_scene_joint_attention_heatmap.shape)
        pred_y_mid_final, pred_x_mid_final = np.unravel_index(np.argmax(final_joint_attention_heatmap), final_joint_attention_heatmap.shape)

        if cfg.exp_params.vis_dist_error:
            gt_x_min, gt_y_min, gt_x_max, gt_y_max = map(float, gt_box[0])
            gt_x_min, gt_x_max = map(lambda x:x*cfg.exp_set.resize_width, [gt_x_min, gt_x_max])
            gt_y_min, gt_y_max = map(lambda y:y*cfg.exp_set.resize_height, [gt_y_min, gt_y_max])
            gt_x_mid, gt_y_mid = (gt_x_min+gt_x_max)/2, (gt_y_min+gt_y_max)/2
            pred_y_mid, pred_x_mid = np.unravel_index(np.argmax(person_person_joint_attention_heatmap), person_person_joint_attention_heatmap.shape)
            l2_dist_x = ((gt_x_mid-pred_x_mid)**2)**0.5
            l2_dist_y = ((gt_y_mid-pred_y_mid)**2)**0.5
            l2_dist_euc = (l2_dist_x**2+l2_dist_y**2)**0.5
            pred_ja_vid[img_idx] = np.array([pred_x_mid, pred_y_mid])
            gt_ja_vid[img_idx] = np.array([gt_x_mid, gt_y_mid])
            # print(l2_dist_euc)

        person_person_joint_attention_heatmap = cv2.applyColorMap(person_person_joint_attention_heatmap, cv2.COLORMAP_JET)
        person_scene_joint_attention_heatmap = cv2.applyColorMap(person_scene_joint_attention_heatmap, cv2.COLORMAP_JET)
        final_joint_attention_heatmap = cv2.applyColorMap(final_joint_attention_heatmap, cv2.COLORMAP_JET)
        person_person_joint_attention_heatmap = cv2.addWeighted(img, 0.5, person_person_joint_attention_heatmap, 0.5, 0)
        person_scene_joint_attention_heatmap = cv2.addWeighted(img, 0.5, person_scene_joint_attention_heatmap, 0.5, 0)
        final_joint_attention_heatmap = cv2.addWeighted(img, 0.5, final_joint_attention_heatmap, 0.5, 0)
        whole_image_gaze = cv2.addWeighted(img, 1.0, img, 0.0, 0)
        whole_image_action = cv2.addWeighted(img, 1.0, img, 0.0, 0)

        # plot estimated and groung-truth joint attentions
        # cv2.circle(person_person_joint_attention_heatmap, (pred_x_mid_p_p, pred_y_mid_p_p), 10, (0, 165, 255), thickness=-1)
        # cv2.circle(person_person_joint_attention_heatmap, (int(gt_x_mid), int(gt_y_mid)), 10, (0, 255, 0), thickness=-1)
        # cv2.circle(person_scene_joint_attention_heatmap, (pred_x_mid_p_s, pred_y_mid_p_s), 10, (0, 165, 255), thickness=-1)
        # cv2.circle(person_scene_joint_attention_heatmap, (int(gt_x_mid), int(gt_y_mid)), 10, (0, 255, 0), thickness=-1)
        cv2.circle(final_joint_attention_heatmap, (pred_x_mid_final, pred_y_mid_final), 10, (0, 165, 255), thickness=-1)
        cv2.circle(final_joint_attention_heatmap, (int(gt_x_mid), int(gt_y_mid)), 10, (0, 255, 0), thickness=-1)

        if cfg.data.name == 'volleyball':
            thickness_data = 3
            fontscale_data = 3.0
        else:
            thickness_data = 2
            fontscale_data = 1.0

        # cv2.putText(final_joint_attention_heatmap, text=f'GT', org=(int(gt_x_mid)+20, int(gt_y_mid)+20), color=(0, 255, 0),
            # fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=fontscale_data, thickness=thickness_data, lineType=cv2.LINE_4)

        # save an attention estimation as a superimposed image
        key_no_padding_num = torch.sum((torch.sum(head_feature, dim=-1) != 0)).numpy()
        for person_idx in range(key_no_padding_num):
            # load heatmaps
            person_person_att = cv2.imread(os.path.join(save_image_dir_dic['person_person_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_person_att.png'), cv2.IMREAD_GRAYSCALE)
            person_scene_att = cv2.imread(os.path.join(save_image_dir_dic['person_scene_att'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_scene_att.png'), cv2.IMREAD_GRAYSCALE)
            person_person_att = cv2.resize(person_person_att, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
            person_scene_att = cv2.resize(person_scene_att, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
            person_person_att = norm_heatmap(person_person_att).astype(np.uint8)
            person_scene_att = norm_heatmap(person_scene_att).astype(np.uint8)
            person_person_att = cv2.applyColorMap(person_person_att, cv2.COLORMAP_JET)
            person_scene_att = cv2.applyColorMap(person_scene_att, cv2.COLORMAP_JET)
            person_person_att = cv2.addWeighted(img, 0.5, person_person_att, 0.5, 0)
            person_scene_att = cv2.addWeighted(img, 0.5, person_scene_att, 0.5, 0)

            # get person location and gt location
            head_feature_person = head_feature[person_idx]
            head_x, head_y = head_feature_person[0:2]
            action_vector = head_feature_person[2:]
            head_x, head_y = int(head_x*cfg.exp_set.resize_width), int(head_y*cfg.exp_set.resize_height)
            gt_mid_x, gt_mid_y = (gt_box[person_idx, 0]+gt_box[person_idx, 2])/2, (gt_box[person_idx, 1]+gt_box[person_idx, 3])/2
            gt_mid_x, gt_mid_y = int(gt_mid_x*cfg.exp_set.resize_width), int(gt_mid_y*cfg.exp_set.resize_height)

            # gaze estimation
            gaze_vec_x, gaze_vec_y = head_vector[person_idx, 0:2]
            gaze_l = 50
            gaze_x, gaze_y = int(head_x+gaze_vec_x*gaze_l), int(head_y+gaze_vec_y*gaze_l)
            gaze_color = (255, 255, 255)
            # gaze_color = (0, 0, 0)
            gaze_size = 2
            # cv2.arrowedLine(person_person_joint_attention_heatmap, (head_x, head_y), (gaze_x, gaze_y), gaze_color, gaze_size)
            # cv2.arrowedLine(person_scene_joint_attention_heatmap, (head_x, head_y), (gaze_x, gaze_y), gaze_color, gaze_size)
            # cv2.arrowedLine(final_joint_attention_heatmap, (head_x, head_y), (gaze_x, gaze_y), gaze_color, gaze_size)
            cv2.arrowedLine(whole_image_gaze, (head_x, head_y), (gaze_x, gaze_y), gaze_color, gaze_size)

            if cfg.data.name == 'volleyball':
                # action prediction
                action_idx = np.argmax(action_vector.numpy())
                action_idx = action_idx_to_name(action_idx)
                action_color = (255, 255, 255)
                action_size = 2
                action_shift = 20

                # cv2.putText(person_person_joint_attention_heatmap, text=f'{action_idx}', org=(head_x, head_y-action_shift), color=action_color,
                #             fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0, thickness=action_size, lineType=cv2.LINE_4)
                # cv2.putText(person_scene_joint_attention_heatmap, text=f'{action_idx}', org=(head_x, head_y-action_shift), color=action_color,
                #             fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0, thickness=action_size, lineType=cv2.LINE_4)
                # cv2.putText(final_joint_attention_heatmap, text=f'{action_idx}', org=(head_x, head_y-action_shift), color=action_color,
                #             fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0, thickness=action_size, lineType=cv2.LINE_4)
                cv2.putText(whole_image_action, text=f'{action_idx}', org=(head_x, head_y-action_shift), color=action_color,
                            fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0, thickness=action_size, lineType=cv2.LINE_4)

            cv2.circle(person_person_att, (gt_mid_x, gt_mid_y), 10, (0, 255, 0), thickness=-1)
            cv2.circle(person_scene_att, (gt_mid_x, gt_mid_y), 10, (0, 255, 0), thickness=-1)
            cv2.line(person_person_att, (head_x, head_y), (gt_mid_x, gt_mid_y), (0, 255, 0), 1)
            cv2.line(person_scene_att, (head_x, head_y), (gt_mid_x, gt_mid_y), (0, 255, 0), 1)

            head_x_min, head_y_min, head_x_max, head_y_max = map(float, head_bbox[person_idx])
            head_x_min, head_x_max = map(lambda x: int(x*img.shape[1]), [head_x_min, head_x_max])
            head_y_min, head_y_max = map(lambda x: int(x*img.shape[0]), [head_y_min, head_y_max])

            # save image
            cv2.imwrite(os.path.join(save_image_dir_dic['person_person_att_superimposed'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_person_att_superimposed.png'), person_person_att)
            cv2.imwrite(os.path.join(save_image_dir_dic['person_scene_att_superimposed'], data_type_id, data_id_vid, f'{mode}_{data_id}_{person_idx}_person_scene_att_superimposed.png'), person_scene_att)
            # if cfg.exp_params.vis_dist_error:
                # cv2.rectangle(person_person_joint_attention_heatmap, (head_x_min, head_y_min), (head_x_max, head_y_max), (128, 0, 128), thickness=5)
                # cv2.rectangle(person_scene_joint_attention_heatmap, (head_x_min, head_y_min), (head_x_max, head_y_max), (128, 0, 128), thickness=5)
                # cv2.rectangle(final_joint_attention_heatmap, (head_x_min, head_y_min), (head_x_max, head_y_max), (128, 0, 128), thickness=5)

        cv2.imwrite(os.path.join(save_image_dir_dic['person_person_jo_att_superimposed'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_person_jo_att_superimposed.png'), person_person_joint_attention_heatmap)
        cv2.imwrite(os.path.join(save_image_dir_dic['person_scene_jo_att_superimposed'], data_type_id, data_id_vid, f'{mode}_{data_id}_person_scene_jo_att_superimposed.png'), person_scene_joint_attention_heatmap)
        cv2.imwrite(os.path.join(save_image_dir_dic['final_jo_att_superimposed'], data_type_id, data_id_vid, f'{mode}_{data_id}_final_jo_att_superimposed.png'), final_joint_attention_heatmap)

    # generate a video of sequences
    generate_video_type_list = []
    generate_video_type_list.append('person_person_jo_att_superimposed')
    generate_video_type_list.append('person_scene_jo_att_superimposed')
    generate_video_type_list.append('final_jo_att_superimposed')
    for generate_video_type in generate_video_type_list:
        frames = []
        for img_idx, img_path in enumerate(tqdm(out['rgb_path'])):
            # data_id = data_id_generator(img_path[0], cfg)
            frame = cv2.imread(os.path.join(save_image_dir_dic[generate_video_type], data_type_id, data_id_vid, f'{mode}_{data_id}_{generate_video_type}.png'))
            frames.append(cv2.resize(frame, (original_width, original_height)))
        video_path = os.path.join(save_image_dir_dic[f'{generate_video_type}_video'], data_type_id, f'{data_id_vid}.mp4')
        generate_video_from_frames(frames, video_path, fps=5)

        # remove frames after generating video
        # for img_idx, img_path in enumerate(tqdm(out['rgb_path'])):
            # data_id = data_id_generator(img_path[0], cfg)
            # os.remove(os.path.join(save_image_dir_dic[generate_video_type], data_type_id, data_id_vid, f'{mode}_{data_id}_{generate_video_type}.png'))

    # plot estimated and groung-truth joint attentions
    if cfg.exp_params.vis_dist_error:
        pred_ja_vid[:, 1] = cfg.exp_set.resize_height - pred_ja_vid[:, 1]
        gt_ja_vid[:, 1] = cfg.exp_set.resize_height - gt_ja_vid[:, 1]
        plt.figure()
        plt.plot(pred_ja_vid[:, 0], pred_ja_vid[:, 1], 'o', label='pred')
        plt.plot(gt_ja_vid[:, 0], gt_ja_vid[:, 1], 'o', label='gt')
        plt.legend()
        plt.savefig(os.path.join(save_image_dir_dic['person_person_jo_att_superimposed_traj'], data_type_id, data_id_vid, f'{mode}_{data_id_vid}_ja_traj.png'))
        plt.close()

        pred_ja_vid_diff = pd.DataFrame(pred_ja_vid).diff().fillna(0)
        plt.figure()
        for t in range(pred_ja_vid_diff.shape[0]):
            t_norm = str(t / (pred_ja_vid_diff.shape[0]))
            plt.plot(pred_ja_vid_diff.iloc[t, 0], pred_ja_vid_diff.iloc[t, 1], 'o', color=t_norm)
        plt.xlim(-50, 50)
        plt.ylim(-50, 50)
        plt.savefig(os.path.join(save_image_dir_dic['person_person_jo_att_superimposed_traj'], data_type_id, data_id_vid, f'{mode}_{data_id_vid}_ja_traj_pred.png'))
        plt.close()

        gt_ja_vid_diff = pd.DataFrame(gt_ja_vid).diff().fillna(0)
        plt.figure()
        for t in range(gt_ja_vid_diff.shape[0]):
            t_norm = str(t / (gt_ja_vid_diff.shape[0]))
            plt.plot(gt_ja_vid_diff.iloc[t, 0], gt_ja_vid_diff.iloc[t, 1], 'o', color=t_norm)
        plt.xlim(-50, 50)
        plt.ylim(-50, 50)
        plt.savefig(os.path.join(save_image_dir_dic['person_person_jo_att_superimposed_traj'], data_type_id, data_id_vid, f'{mode}_{data_id_vid}_ja_traj_gt.png'))
        plt.close()
    
    cfg.exp_set.resize_height = resize_height
    cfg.exp_set.resize_width = resize_width