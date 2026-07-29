from PIL import Image
import pytesseract
import numpy as np
import matplotlib.pyplot as plt
import cv2
from matplotlib.patches import Polygon

def blur_image(im, k=None, blur_param=0.00425): 
    # found empirical best fit, defaulted the blur parameter to match
    h, w = im.shape
    
    if k is None: 
        k = int(2 * round(min(h, w) * blur_param) + 1.)
        
    blurred = cv2.GaussianBlur(im, (k, k), 0)
    return blurred

def find_page(im, kernel_size=(5,5), blur_param=0.005): 
    h, w = im.shape
    if kernel_size is None: 
        k = int(2 * round(min(h, w) * blur_param) + 1.)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, kernel_size)
    closed = cv2.morphologyEx(im, cv2.MORPH_CLOSE, kernel)    
    
    
    contours, hierarchy = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return closed, contours, hierarchy

def is_page_like(contour, img_shape, min_frac=0.5):                                                                   
      x, y, w, h = cv2.boundingRect(contour)         
      H, W = img_shape                                                                                                  
      return w > min_frac * W and h > min_frac * H  

def draw_page(contours, ax): 
    page = max(contours, key=cv2.contourArea) 
    det_contour = cv2.minAreaRect(page)
    box = cv2.boxPoints(det_contour); box = np.intp(box)
    
    polygon = Polygon(box, closed=True, facecolor='white', alpha=0.3)
    ax.add_patch(polygon)
    
    return ax

def get_binary_image(im, lo_thresh=0, hi_thresh=255, foreground_frac_lo=0.02, foreground_frac_hi = 0.3, adaptive_theshold_block_size=31, adaptive_threshold_C=10): 
    # todo optimize adaptive threshold params if we need to
    
    # run otsu
    _, total_binary = cv2.threshold(im, lo_thresh, hi_thresh, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    h, w = im.shape[:2]
    mid_h = h // 2
    mid_w = w // 2

    top_left = total_binary[0:mid_h, 0:mid_w]
    top_right = total_binary[0:mid_h, mid_w:w]
    bottom_left = total_binary[mid_h:h, 0:mid_w]
    bottom_right = total_binary[mid_h:h, mid_w:w]
    
    sub_images = [top_left, top_right, bottom_left, bottom_right, total_binary]
    for image in sub_images: 
        frac = np.mean(image > 0)
        if (frac < foreground_frac_lo) or (frac > foreground_frac_hi): 
            print('problem, falling back to adaptive threshold')
            return cv2.adaptiveThreshold(im, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, adaptive_theshold_block_size, adaptive_threshold_C) 
    
    return total_binary

def clean_binary_image(binary_image, quantiles_to_keep=[0.05, 0.95], plot=True):
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary_image, connectivity=8)  
    
    if plot:
        fig = plt.figure()
        areas = stats[1:, cv2.CC_STAT_AREA]
        plt.hist(areas, bins=np.logspace(0, np.log10(areas.max()+1), 50))
        plt.xscale('log')   
        plt.axvline(np.quantile(areas.flatten(), quantiles_to_keep[0]), color="tab:red", ls='--')
        plt.axvline(np.quantile(areas.flatten(), quantiles_to_keep[1]), color="tab:red", ls='--')
        plt.xlabel('detected area (pixels^2)')
        plt.ylabel('count')
        plt.show()
    
    lo_area, hi_area = np.quantile(areas, [0.05, 0.95])
    keepers = (areas >= lo_area) & (areas < hi_area)
    keeper_indices = np.where(keepers)[0] + 1 # because we cut the 0th area (background component)
        
    cleaned_binary_image = np.isin(labels, keeper_indices).astype(np.uint8) * 255
    return cleaned_binary_image

def deskew_image(cleaned_binary_image, angle_lims=[-2, 2], angle_step=0.1): 
    foreground_pixel_coords = np.column_stack(np.where(cleaned_binary_image > 0)) # gives pixels in (y, x)
    foreground_pixel_coords = (foreground_pixel_coords[:, ::-1]).astype(np.float32)
    center, dims, angle = cv2.minAreaRect(foreground_pixel_coords)
    if angle < -45: angle += 90
    print('this is the coarse angle = ', angle)
    
    angle_lo = angle + angle_lims[0]; angle_hi = angle + angle_lims[1]
    n_steps = int(np.round((angle_hi - angle_lo)/angle_step))
    
    angles_to_try = np.linspace(angle_lo, angle_hi, n_steps)
    scores = []
    for ang in angles_to_try: 
        rot_mat_i = cv2.getRotationMatrix2D(center, ang, 1.0)
        im = cv2.warpAffine(cleaned_binary_image, rot_mat_i, (cleaned_binary_image.shape[1], cleaned_binary_image.shape[0]))
        var = np.var(np.sum(im, axis=1))
        scores.append(var)
        
    best = angles_to_try[np.argmax(scores)]
    print('this was our best angle: ', best)
    rot_mat = cv2.getRotationMatrix2D(center, best, 1.0)
    deskewed_image = cv2.warpAffine(cleaned_binary_image, rot_mat, (cleaned_binary_image.shape[1], cleaned_binary_image.shape[0]))
    return deskewed_image

def image_segmentation(im_arr, plot=False): 
    proj_x = np.sum(im_arr, axis=0)
    
    # find column boundary
    proj_x_prime = np.diff(proj_x)
    zero_inds = np.where(proj_x_prime == 0)
    column_pos = int(np.round(np.mean(zero_inds)))
    
    if plot: 
        fig = plt.figure()
        plt.plot(proj_x)
        plt.xlabel('Pixel num (x axis)')
        plt.ylabel('Summed value (proportional to amount of ink)')
        plt.axvline(column_pos, color='k', ls='--')
        plt.show()
    
    left_side_image = im_arr[:, 0:column_pos-1]
    right_side_image = im_arr[:, column_pos:]
    image_segments = [left_side_image, right_side_image]
    return image_segments

def get_text_from_image(image, split=True): 
    if split: 
        image_segments = image_segmentation(image)
        results = [pytesseract.image_to_string(im) for im in image_segments]
    else: 
        results = pytesseract.image_to_string(image)

    return results

if __name__ == "__main__":
    chicken_recipe_photo = '../data/photos/20260404_204704_04f01b.jpg'
    im = Image.open(chicken_recipe_photo)
    gray_arr = np.array(im.convert('L')) # y axis is 0, x axis is 1
    
    lo = np.quantile(gray_arr.flatten(), 0.16)
    hi = np.quantile(gray_arr.flatten(), 0.84)
    
    # first plot a histogram of the pixel values
    '''
    fig = plt.figure()
    plt.hist(gray_arr.flatten())
    plt.axvline(lo, color='tab:red', ls='--')
    plt.axvline(hi, color='tab:red', ls='--')
    plt.xlabel('Pixel values (amount of ink)')
    plt.ylabel('Count')
    plt.show()
    '''
        
    #gray_arr = blur_image(gray_arr)
    canny_out = cv2.Canny(gray_arr, lo, hi)
    rect, contours, hierarchy = find_page(canny_out)
    
    binary_image = get_binary_image(gray_arr)
    cleaned_binary_image = clean_binary_image(binary_image)
    deskewed_image = deskew_image(cleaned_binary_image)
        
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].imshow(gray_arr); axes[0].set_title('original image')
    axes[1].imshow(cleaned_binary_image); axes[1].set_title('cleaned binary image')
    axes[2].imshow(deskewed_image); axes[2].set_title('deskewed image')
    plt.show()
    
    upsample_fac = 5
    upsampled_image = cv2.resize(gray_arr, None, fx=upsample_fac, fy=upsample_fac, interpolation=cv2.INTER_CUBIC)
    
    up_clean_image = get_binary_image(upsampled_image)
    up_clean_image = clean_binary_image(up_clean_image, quantiles_to_keep=[0.05, 0.99])
    up_clean_image = deskew_image(up_clean_image)
    
    labels = ['original', 'cleaned', 'deskewed', 'upsampled', 'upsampled_cleaned']
    for i, im in enumerate([gray_arr, cleaned_binary_image, deskewed_image, upsampled_image, up_clean_image]): 
        filename = './tesseract_'+labels[i]+'.txt'
        results = get_text_from_image(im)
        with open(filename, 'w') as f: 
            for res in results: 
                f.write(res+'\n')
        print('saved ', filename)