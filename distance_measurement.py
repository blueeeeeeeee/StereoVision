import os
os.environ['KMP_DUPLICATE_LIB_OK']='True'
import sys
"""
需要更改为自己的路径
"""
sys.path.append(r'C:\Data\Research\work\StereoVision\efficientvit')

# Package importation
import numpy as np
import cv2
import math
import time
from skimage.filters import gaussian
from skimage.segmentation import active_contour
from scipy.ndimage import binary_fill_holes
from ultralytics import YOLO
import matplotlib.pyplot as plt
from efficientvit.sam_model_zoo import create_sam_model
from efficientvit.models.efficientvit.sam import EfficientViTSamPredictor
from fitter import Fitter
from fitter import HistFit
from pylab import hist



"""
以下变量需要每次跑代码前都进行检查更改
"""
id_image=0 # 保存标定图片的开始序号
image_width = 2160 # 视频流图像的宽度（两张一起）
image_height = 1080 # 视频流图像的高度
checkerboard_long = 11 # 标定板的宽度上有多少个角点
checkerboard_short = 8 # # 标定板的高度上有多少个角点
pics_folder = "C:\\Data\\Research\\work\\StereoVision\\checkerboard\\" # 拍照文件目录
save_folder = r"C:\Data\Research\work\StereoVision\results" # 存放相机参数目录
left_map_file=os.path.join(save_folder, 'Left_Stereo_Map.npz')
right_map_file=os.path.join(save_folder, 'Right_Stereo_Map.npz')
Q_file=os.path.join(save_folder, 'Q.npy')
checker_size = 15 # 方格边长15mm 
checkerboard_start_num = 0 # 标定图片的开始序号
checkerboard_end_num = 102 # 标定图片的结束序号

"""
以下是用于计算距离的超参数 一般不更改
"""
precision = 4 # mm
target_window = 25 # 像素块边长
# Filtering
kernel= np.ones((3,3),np.uint8)

def active_contour_mask(gray_image, init_border):
    print("active contour")
    # print(type(init_border))
    # print(init_border.shape)
    snake = active_contour(
        gaussian(gray_image, sigma=3, preserve_range=False),
        init_border,
        alpha=0.015,
        beta=10,
        gamma=0.001,
    ).astype(int)
    mask = np.zeros_like(gray_image)
    mask[snake[:,0],snake[:,1]] = 1

    mask = binary_fill_holes(mask)
    # cv2.imshow('mask of target object', )
    # cv2.imshow('init border of target object', )
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(gray_image, cmap=plt.cm.gray)
    ax.plot(init_border[:, 0], init_border[:, 1], '--r', lw=3)
    ax.plot(snake[:, 0], snake[:, 1], '-b', lw=3)
    # ax.set_xticks([]), ax.set_yticks([])
    # ax.axis([0, gray_image.shape[1], gray_image.shape[0], 0])

    plt.show()

    return mask


def show_mask(mask, ax, random_color=False):
    if random_color:
        color = np.concatenate([np.random.random(3), np.array([0.6])], axis=0)
    else:
        color = np.array([30/255, 144/255, 255/255, 0.6])
    h, w = mask.shape[-2:]
    # print("mask height: ", h, " mask width: ", w)
    mask_image = mask.reshape(h, w, 1) * color.reshape(1, 1, -1)
    ax.imshow(mask_image)


def get_target_depth(depth, depth_filter, image_shape, row, col, bbox, predictor, image):
    # 根据原始视差图和滤波后的视差图来计算得到目标物的距离
    # left_image 是灰度图
    # 得到目标物深度可能正确的范围值
    start_time = time.time()
    range_std = 2 # 该值可能需要再调整
    # image_border = np.zeros_like(left_image)
    image_border = get_image_border(bbox, image_shape)
    """
    注意这里需要修改image_border, 根据传入的bbox来确定 左上角的hw + height width
    """
    # h, w, height, width = bbox
    # image_border[h:h+height,w:w+width] = 1
    # image_border[h+1:h+height-1,w+1:w+width-1] = 0
    
    depth_right = np.where((depth > 0) & (depth_filter > 0) & (np.abs(depth - depth_filter) < precision), (depth + depth_filter) / 2, 0)
    # print("depth_right.max(): ", depth_right.max())
    # print("depth_right.min(): ", depth_right.min())
    
    """
    下面的代码太苛刻了，感觉可以不用
    """
    # depth_border = None
    # if image_border is not None:
    #     depth_border = depth_right[np.nonzero(np.where(image_border, depth_right, 0))]
    # if depth_border is not None:
    #     depth_right = np.where((depth_right > depth_border.mean() - range_std * depth_border.std()) & (depth_right < depth_border.mean() + range_std * depth_border.std()), 0, depth_right)

    # 方法一：直接给出depth_right的中位数/平均数
    # target_bbox是根据鼠标点击点来计算的，感觉不太对，还是要用轮廓
    if image_border is None:
        print("using traditional method")
        target_bbox = np.zeros(image_shape)
        target_bbox[row - target_window: row + target_window, col - target_window: col + target_window] = 1
        depth_right_window = np.where((target_bbox > 0) & (depth_right > 0), depth_right, 0)
        target_index = np.nonzero(depth_right_window)
        if target_index is not None:
            target_depth = np.median(depth_right_window[target_index])
            target_depth = (depth_right_window[target_index]).mean()
        else:
            target_depth = (depth_filter[row - target_window: row + target_window, col - target_window: col + target_window]).mean()
    else:
        # 方法二：根据left_image得到target的mask，然后返回depth的中位数/平均值 -- 采用active contour的方法
        print("using efficientvit sam")
        # target_mask = active_contour_mask(left_image, np.column_stack(np.nonzero(image_border)))
        start_time_sam = time.time()
        predictor.set_image(image)
        target_mask, _, _ = predictor.predict(
            point_coords=None,
            point_labels=None,
            box=bbox[None, :],
            multimask_output=False,
        )
        target_mask = target_mask[0]
        end_time_sam = time.time()
        print("spending time (efficient sam): {:.2f}秒".format(end_time_sam - start_time_sam))
        plt.figure(figsize=(10, 10))
        plt.imshow(image)
        show_mask(target_mask, plt.gca())
        show_box(bbox, plt.gca())
        plt.title("efficientvit sam")
        plt.axis('off')
        plt.show()
        tmp_depth = (depth_filter[np.nonzero(target_mask)]).mean()
        target_mask = np.where(target_mask & (depth_right > 0), 1, 0)
        target_index = np.nonzero(target_mask)
        # plt.hist(depth_right[target_index], bins=10)
        # plt.show()
        if target_index is not None:
            # Y, X, _ = hist(depth_right[target_index], bins=10)
            # hf = HistFit(X=X, Y=Y)
            # hf.fit(error_rate=0.03, Nfit=20)
            # print(hf.mu, hf.sigma, hf.amplitude)
            print("target index is not none")
            # target_depth = np.median(depth_right[target_index])
            if depth_right[target_index] is None:
                target_depth = tmp_depth
            else:
                target_depth = (depth_right[target_index]).mean()
            # target_depth = hf.mu
        else:
            target_depth =  tmp_depth
    
    end_time = time.time()
    print("spending time (active contour): {:.2f}秒".format(end_time - start_time))

    return target_depth



# 两种计算方法都行，看哪个的结果准确一点 -- 现在用的是第一个
def get_depth(disparity, Q, scale=1, method=True):
    """
    reprojectImageTo3D(disparity, Q),输入的Q,单位必须是毫米(mm)
    :param disparity: 视差图
    :param Q: 重投影矩阵Q=[[1, 0, 0, -cx]
                        [0, 1, 0, -cy]
                        [0, 0, 0,  f]
                        [1, 0, -1/Tx, (cx-cx`)/Tx]]
        其中f为焦距, Tx相当于平移向量T的第一个参数
    :param scale: 单位变换尺度,默认scale=1.0,单位为毫米
    :return depth:ndarray(np.uint16),depth返回深度图, 即距离
    """
    # 将图片扩展至3d空间中，其z方向的值则为当前的距离
    if method:
        points_3d = cv2.reprojectImageTo3D(disparity, Q)  # 单位是毫米(mm)
        x, y, depth = cv2.split(points_3d) # 三维世界的坐标
        # print("x: ", x)
        # print("y: ", y)
    else:
        # baseline = abs(camera_config["T"][0])
        baseline = 1 / Q[3, 2]  # 基线也可以由T[0]计算
        fx = abs(Q[2, 3])
        depth = (fx * baseline) / disparity
    depth = depth * scale
    depth = np.asarray(depth, dtype=np.float32) # 单位是 mm
    x = x * scale
    x = np.asarray(x, dtype=np.float32) # 单位是 mm
    y = y * scale
    y = np.asarray(y, dtype=np.float32) # 单位是 mm
    return depth, x, y


def get_image_border(bbox, image_shape):
    if bbox is None:
        return None
    bbox_xmin = bbox[0]
    bbox_ymin = bbox[1]
    bbox_xmax = bbox[2]
    bbox_ymax = bbox[3]
    image_bbox = np.zeros(image_shape)
    image_bbox[bbox_ymin:bbox_ymax, bbox_xmin:bbox_xmax] = 1
    image_bbox[bbox_ymin+1:bbox_ymax-1, bbox_xmin+1:bbox_xmax-1] = 0

    return image_bbox


def get_target_bbox(bboxes, x, y):
    target_id = -1
    target_size = image_height * image_width
    for bbox_id in range(bboxes.shape[0]):
        bbox = bboxes[bbox_id]
        bbox_xmin = bbox[0]
        bbox_ymin = bbox[1]
        bbox_xmax = bbox[2]
        bbox_ymax = bbox[3]
        if x > bbox_xmin and y > bbox_ymin and x < bbox_xmax and y < bbox_ymax:
            bbox_size = (bbox_xmax - bbox_xmin) * (bbox_ymax - bbox_ymin)
            if bbox_size < target_size:
                target_id = bbox_id
                target_size = bbox_size

    if target_id > -1:
        # image_bbox = np.zeros((image_height, int(image_width / 2)))
        # image_bbox[bbox_ymin:bbox_ymax, bbox_xmin:bbox_xmax] = 1
        # image_bbox[bbox_ymin+1:bbox_ymax-1, bbox_xmin+1:bbox_xmax-1] = 0

        # remap_image_bbox = cv2.remap(image_bbox,remap_matrixs[0],remap_matrixs[1], interpolation = cv2.INTER_LANCZOS4, borderMode = cv2.BORDER_CONSTANT)  # Rectify the image using the kalibration parameters founds during the initialisation

        # difference = cv2.subtract(image_bbox, remap_image_bbox)
        # print(difference)
        # result = not np.any(difference) #if difference is all zeros it will return False
        
        # if result is True:
        #     print("两张图片一样")
        # else:
        #     cv2.imwrite(r"C:\Data\Research\work\StereoVision\test_results\result.jpg", difference)
        #     print ("两张图片不一样")
        return bboxes[target_id]
    else:
        return None
    

def show_box(box, ax):
    x0, y0 = box[0], box[1]
    w, h = box[2] - box[0], box[3] - box[1]
    ax.add_patch(plt.Rectangle((x0, y0), w, h, edgecolor='green', facecolor=(0,0,0,0), lw=2))    


def coords_mouse_disp(event,x,y,flags,param):
    # print(type(target_bbox))
    # print(target_bbox.shape)
    if event == cv2.EVENT_LBUTTONDBLCLK:
        points_depth, filtered_points_depth, image_shape, bboxes, left_nice, predictor, points_x, points_y, filtered_points_x, filtered_points_y = param
        left_nice = cv2.cvtColor(left_nice, cv2.COLOR_BGR2RGB)
        target_bbox = get_target_bbox(bboxes, x, y)
        if target_bbox is not None:
            plt.figure(figsize=(10, 10))
            plt.imshow(left_nice)
            # show_mask(masks[0], plt.gca())
            show_box(target_bbox, plt.gca())
            plt.title("target bbox")
            plt.axis('off')
            plt.show()
        print("y: ", y, "x: ", x, "Distance not filter: ", points_depth[y,x])
        print("y: ", y, "x: ", x, "Distance using filter: ", filtered_points_depth[y,x])
        print("y: ", y, "x: ", x, "x not filter: ", points_x[y,x])
        print("y: ", y, "x: ", x, "x using filter: ", filtered_points_x[y,x])
        print("y: ", y, "x: ", x, "y not filter: ", points_y[y,x])
        print("y: ", y, "x: ", x, "y using filter: ", filtered_points_y[y,x])
        print('target_depth: ', get_target_depth(points_depth, filtered_points_depth, image_shape, y, x, target_bbox, predictor, left_nice))
        
        
def stereo_calibration(checkerboard_long, checkerboard_short, checker_size, checkerboard_start_num, checkerboard_end_num, pic_folder, save_folder):
    # Termination criteria
    criteria =(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    criteria_stereo= (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    # Prepare object points
    # 世界坐标系
    objp = np.zeros((checkerboard_long*checkerboard_short,3), np.float32)
    objp[:,:2] = (np.mgrid[0:checkerboard_long,0:checkerboard_short].T.reshape(-1,2)) * checker_size

    # Arrays to store object points and image points from all images
    objpoints= []   # 3d points in real world space
    # objpoints_test= [] 
    imgpointsR= []   # 2d points in image plane
    imgpointsL= []

    # Start calibration from the camera
    print('Reading checkerboard pictures ... ')
    # Call all saved images
    for i in range(checkerboard_start_num, checkerboard_end_num + 1):   # Put the amount of pictures you have taken for the calibration inbetween range(0,?) wenn starting from the image number 0
        t= str(i)
        ChessImaR= cv2.imread(os.path.join(pic_folder, 'chessboard-R'+t+'.png'),0)    # Right side
        ChessImaL= cv2.imread(os.path.join(pic_folder, 'chessboard-L'+t+'.png'),0)    # Left side
        # print(ChessImaR.shape)
        retR, cornersR = cv2.findChessboardCorners(ChessImaR,
                                                (checkerboard_long,checkerboard_short),None)  # Define the number of chees corners we are looking for
        retL, cornersL = cv2.findChessboardCorners(ChessImaL,
                                                (checkerboard_long,checkerboard_short),None)  # Left side
        if (True == retR) & (True == retL):
            objpoints.append(objp)
            cornersR = cv2.cornerSubPix(ChessImaR,cornersR,(11,11),(-1,-1),criteria)
            cornersL = cv2.cornerSubPix(ChessImaL,cornersL,(11,11),(-1,-1),criteria)
            imgpointsR.append(cornersR)
            imgpointsL.append(cornersL)


    print('Starting calibration for the 2 cameras... ')
    # Determine the new values for different parameters
    #   Right Side
    retR, mtxR, distR, rvecsR, tvecsR = cv2.calibrateCamera(objpoints,
                                                            imgpointsR,
                                                            ChessImaR.shape[::-1],None,None)

    # optimize Omtx
    # hR,wR= ChessImaR.shape[:2]
    # OmtxR, roiR= cv2.getOptimalNewCameraMatrix(mtxR,distR,
                                                    # (wR,hR),1,(wR,hR))

    #   Left Side
    retL, mtxL, distL, rvecsL, tvecsL = cv2.calibrateCamera(objpoints,
                                                            imgpointsL,
                                                            ChessImaL.shape[::-1],None,None)

    # optimize Omtx
    # hL,wL= ChessImaL.shape[:2]
    # OmtxL, roiL= cv2.getOptimalNewCameraMatrix(mtxL,distL,(wL,hL),1,(wL,hL))

    # scale_pixel = 3 / ((mtxL[0,0] + mtxL[1,1] + mtxR[0,0] + mtxR[1,1]) / 4) # 用于验证，实际计算过程中不需要
    # print("get the scale ( 1 pixel x mm): ", scale_pixel)

    print('Cameras Ready to use')

    retS, MLS, dLS, MRS, dRS, R, T, E, F= cv2.stereoCalibrate(objpoints,
                                                            imgpointsL,
                                                            imgpointsR,
                                                            mtxL, # 原来的代码是 mtxL
                                                            distL,
                                                            mtxR, # 原来的代码是 mtxR
                                                            distR,
                                                            ChessImaR.shape[::-1],
                                                            criteria = criteria_stereo,
                                                            flags = cv2.CALIB_FIX_INTRINSIC)

    # StereoRectify function
    rectify_scale= 0 # if 0 image croped, if 1 image nor croped
    # 该函数的作用是为每个摄像头计算立体校正的映射矩阵，所以其运行结果并不是直接将图片进行立体矫正，
    # 而是得出进行立体矫正所需要的映射矩阵
    # 立体极线校正
    RL, RR, PL, PR, Q, roiL, roiR= cv2.stereoRectify(MLS, dLS, MRS, dRS,
                                                    ChessImaR.shape[::-1], R, T,
                                                    rectify_scale,(0,0))  # last paramater is alpha, if 0= croped, if 1= not croped

    # initUndistortRectifyMap function
    Left_Stereo_Map= cv2.initUndistortRectifyMap(MLS, dLS, RL, PL,
                                                ChessImaR.shape[::-1], cv2.CV_32FC1)   # cv2.CV_16SC2 this format enables us the programme to work faster
    Right_Stereo_Map= cv2.initUndistortRectifyMap(MRS, dRS, RR, PR,
                                                ChessImaR.shape[::-1], cv2.CV_32FC1)

    np.savez(left_map_file, Left_Stereo_Map_0=Left_Stereo_Map[0], Left_Stereo_Map_1=Left_Stereo_Map[1])
    np.savez(right_map_file, Right_Stereo_Map_0=Right_Stereo_Map[0], Right_Stereo_Map_1=Right_Stereo_Map[1])
    np.save(Q_file, Q)
    print("校正映射矩阵计算完毕，已保存")
#*******************************************
#***** Parameters for the StereoVision *****
#*******************************************
def disparity_calculation(left_map_file, right_map_file, image_height, image_width, Q_file):
    # Create StereoSGBM and prepare all parameters
    window_size = 3
    min_disp = 0
    num_disp = 256-min_disp

    # 用SGBM算法获取视差图，即景深图
    # StereoSGBM的速度比StereoBM慢，但是精度更高，准确性更好
    # 下面的这些参数都是可以调节的，都是超参数，要做实验，以便确定最佳参数，根据具体的摄像机来确定
    # numDisparities必须要能被16整除
    # blockSize是matched block size，它应该为一个奇数，大部分情况下，它在3到11之间
    # P1和P2控制disparity smoothness
    # speckleRange一般来说，1或者2就足够好了

    stereo = cv2.StereoSGBM_create(minDisparity = min_disp,
        numDisparities = num_disp,
        blockSize = window_size,
        uniquenessRatio = 10,
        speckleWindowSize = 100,
        speckleRange = 32, # 原来的代码 speckleRange = 32
        disp12MaxDiff = 1, # 原来的代码为5
        P1 = 8*3*window_size**2,
        P2 = 32*3*window_size**2)

    # Used for the filtered image
    stereoR=cv2.ximgproc.createRightMatcher(stereo) # Create another stereo for right this time

    # WLS FILTER Parameters
    lmbda = 80000
    sigma = 1.8
    visual_multiplier = 1.0
    
    wls_filter = cv2.ximgproc.createDisparityWLSFilter(matcher_left=stereo)
    # 较大的lambda使得filter_img和原图的轮廓更为一致，通常值为8000
    # 较小的sigma使得视差对图片的纹理、噪音更为敏感，通常在0.8到2.0之间
    wls_filter.setLambda(lmbda)
    wls_filter.setSigmaColor(sigma)

    #*************************************
    #***** Starting the StereoVision *****
    #*************************************

    # Call the two cameras
    camera = cv2.VideoCapture(0)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, image_width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, image_height)

    # prepare map function for remp
    left_map = np.load(left_map_file)
    right_map = np.load(right_map_file)
    Left_Stereo_Map = (left_map['Left_Stereo_Map_0'], left_map['Left_Stereo_Map_1'])
    Right_Stereo_Map = (right_map['Right_Stereo_Map_0'], right_map['Right_Stereo_Map_1'])
    Q = np.load(Q_file)

    # Load a model
    model = YOLO('yolov8n.pt')  # pretrained YOLOv8n model

    efficientvit_sam = create_sam_model(
    name="l0", weight_url=r"C:\Data\Research\work\StereoVision\efficientvit\assets\checkpoints\sam\l0.pt",
    )
    efficientvit_sam = efficientvit_sam.eval()
    efficientvit_sam_predictor = EfficientViTSamPredictor(efficientvit_sam)

    if not camera.isOpened():
        exit(1)

    while True:
        # Start Reading Camera images
        ret, frame = camera.read()
        if not ret:
            print("图像获取失败，请按照说明进行问题排查！")
            break
        
        frameL = frame[0:image_height, 0:int(image_width/2)]
        frameR = frame[0:image_height, int(image_width/2):image_width]

        # (result[0]).show()
        # print(type(bboxes))

        # Rectify the images on rotation and alignement
        Left_nice= cv2.remap(frameL,Left_Stereo_Map[0],Left_Stereo_Map[1], interpolation = cv2.INTER_LANCZOS4, borderMode = cv2.BORDER_CONSTANT)  # Rectify the image using the kalibration parameters founds during the initialisation
        Right_nice= cv2.remap(frameR,Right_Stereo_Map[0],Right_Stereo_Map[1], interpolation = cv2.INTER_LANCZOS4, borderMode = cv2.BORDER_CONSTANT)

        # 感觉不是很对，因为原图需要remap，可以先看看效果
        # Left_nice_small = cv2.resize(Left_nice, (image_width / 4, image_height / 2))
        # Right_nice_small = cv2.resize(Right_nice, (image_width / 4, image_height / 2))
        # Run batched inference on a list of images
        result = model(Left_nice)  # return a list of Results objects
        # result = model(Left_nice_small)
        # print(type(result))
        bboxes = ((result[0].boxes.xyxy).int()).detach().numpy()
        # difference = cv2.subtract(frameL, Left_nice)
        # print(difference)
        # result = not np.any(difference) #if difference is all zeros it will return False
        
        # if result is True:
        #     print("两张图片一样")
        # else:
        #     cv2.imwrite(r"C:\Data\Research\work\StereoVision\test_results\result.jpg", difference)
        #     print ("两张图片不一样")
        start_time = time.time()

        # Convert from color(BGR) to gray
        grayR= cv2.cvtColor(Right_nice,cv2.COLOR_BGR2GRAY)
        grayL= cv2.cvtColor(Left_nice,cv2.COLOR_BGR2GRAY)

        # grayR= cv2.cvtColor(Right_nice_small,cv2.COLOR_BGR2GRAY)
        # grayL= cv2.cvtColor(Left_nice_small,cv2.COLOR_BGR2GRAY)
        # cv2.imshow("grayL", grayL)

        # Compute the 2 images for the Depth_image
        disp= stereo.compute(grayL,grayR)#.astype(np.float32)/ 16

        points_depth, points_x, points_y = get_depth(disp, Q, 16, True)
        # print("points_depth shape: ", points_depth.shape)
        # print("points_depth max: ", points_depth.max())
        # print("points_depth min: ", points_depth.min())
        end_time = time.time()

        dispL= disp
        dispR= stereoR.compute(grayR,grayL)

        # Using the WLS filter
        dsp_filtered = wls_filter.filter(dispL,grayL,None,dispR)
        filtered_points_depth, filtered_points_x, filtered_points_y = get_depth(dsp_filtered, Q, 16, True)
        # print("filtered_points_depth shape: ", filtered_points_depth.shape)
        # print("filtered_points_depth max: ", filtered_points_depth.max())
        # print("filtered_points_depth min: ", filtered_points_depth.min())
        # target_depth = get_target_depth(points_depth, filtered_points_depth, grayL)
        end_time_filtered = time.time()

        # filteredImg = dsp_filtered
        # filteredImg = cv2.normalize(src=filteredImg, dst=filteredImg, beta=0, alpha=255, norm_type=cv2.NORM_MINMAX)
        # filteredImg = np.uint8(filteredImg)
        
        # filt_Color= cv2.applyColorMap(filteredImg,cv2.COLORMAP_JET) 

        numDisparities = 6
        # disp8 = cv2.convertScaleAbs(disp, alpha=255.0 / ((numDisparities * 16 + 16) * 16.0))
        # dsp_filtered8 = cv2.convertScaleAbs(dsp_filtered, alpha=255.0 / ((numDisparities * 16 + 16) * 16.0))
        
        # Show the result for the Depth_image
        cv2.imshow('Left_nice', Left_nice)
        # cv2.imshow('Left_nice_small', Left_nice_small)
        # cv2.imshow('Disparity', disp8)
        # cv2.imshow('Disparity Filtered', dsp_filtered8)
        # cv2.imshow('Filtered Color Depth',filt_Color)

        # Mouse click
        cv2.setMouseCallback("Left_nice", coords_mouse_disp, (points_depth, filtered_points_depth, grayL.shape, bboxes, Left_nice, efficientvit_sam_predictor, points_x, points_y, filtered_points_x, filtered_points_y))
        # cv2.setMouseCallback("Left_nice_small", coords_mouse_disp, (points_depth, filtered_points_depth, grayL.shape, bboxes, Left_nice_small, efficientvit_sam_predictor, points_x, points_y, filtered_points_x, filtered_points_y))
        # print("target depth: ", target_depth)
        print("spending time: {:.2f}秒".format(end_time - start_time))
        print("spending time (filtered): {:.2f}秒".format(end_time_filtered - start_time))
        # End the Programme
        if cv2.waitKey(1) & 0xFF == ord(' '):
            break

    # Release the Cameras
    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    stereo_calibration(checkerboard_long,checkerboard_short,checker_size,checkerboard_start_num, checkerboard_end_num, pics_folder, save_folder)
    disparity_calculation(left_map_file=left_map_file, right_map_file=right_map_file, image_height=image_height, image_width=image_width, Q_file=Q_file)