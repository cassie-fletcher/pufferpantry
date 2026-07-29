"""Brainstorm: test new preprocessing ideas for Tesseract OCR.

Prior preprocessing (Otsu + CC-filter + deskew + cubic upsample) hurt OCR
on Sunday Chicken — raw baseline scored best. This script tests a different
set of ideas grounded in the principle that preprocessing should preserve
or enhance edges, not destroy them.

Output: 2x3 plot (original + 5 variants), one text file per variant in
scripts/tesseract_brainstorm_<variant>.txt.

Run from scripts/ directory: python ocr_preprocessing_brainstorm.py
"""
from PIL import Image
import numpy as np
import matplotlib.pyplot as plt
import cv2
import pytesseract
from skimage.filters import threshold_sauvola


# ── Preprocessing functions ──────────────────────────────────────────────────

def preprocess_clahe(gray, clip_limit=2.0, tile_grid_size=(16, 16)):
    """Contrast-Limited Adaptive Histogram Equalization (local contrast boost)."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    return clahe.apply(gray)


def preprocess_bilateral(gray, d=15, sigma_color=75, sigma_space=75):
    """Edge-preserving denoise: bilateral filter."""
    return cv2.bilateralFilter(gray, d=d, sigmaColor=sigma_color, sigmaSpace=sigma_space)


def preprocess_lanczos_upsample(gray, scale=2.0):
    """Upsample 2x with Lanczos interpolation (sharper than cubic on text)."""
    h, w = gray.shape
    new_size = (int(w * scale), int(h * scale))
    return cv2.resize(gray, new_size, interpolation=cv2.INTER_LANCZOS4)


def preprocess_unsharp(gray, amount=1.0, sigma=1.5):
    """Unsharp mask: sharpened = (1+amount)*image - amount*blur(image)."""
    blurred = cv2.GaussianBlur(gray, (0, 0), sigma)
    return cv2.addWeighted(gray, 1 + amount, blurred, -amount, 0)


def preprocess_sauvola(gray, window_size=51, k=0.2):
    """Sauvola adaptive threshold. Returns binary (text=0, background=255)."""
    T = threshold_sauvola(gray, window_size=window_size, k=k)
    return ((gray > T) * 255).astype(np.uint8)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    photo = '../data/photos/20260404_204704_04f01b.jpg'
    im = Image.open(photo)
    gray = np.array(im.convert('L'))  # y axis is 0, x axis is 1

    # Each variant targets a different failure mode of the raw image.
    variants = [
        ('original',  gray),
        ('clahe',     preprocess_clahe(gray)),              # contrast
        ('bilateral', preprocess_bilateral(gray)),          # denoise
        ('lanczos2x', preprocess_lanczos_upsample(gray)),   # resolution
        ('unsharp',   preprocess_unsharp(gray)),            # edge sharpening
        ('sauvola',   preprocess_sauvola(gray)),            # binarization
    ]

    # 2x3 plot
    fig, axes = plt.subplots(2, 3, figsize=(18, 12))
    for ax, (name, img) in zip(axes.flat, variants):
        label = name + (' (binary)' if name == 'sauvola' else '')
        ax.imshow(img, cmap='gray')
        ax.set_title(label)
        ax.axis('off')
    plt.tight_layout()

    # Run Tesseract (default PSM 3) on each variant, save text
    for name, img in variants:
        text = pytesseract.image_to_string(img)
        out_path = f'./tesseract_brainstorm_{name}.txt'
        with open(out_path, 'w') as f:
            f.write(text)
        print(f'wrote {out_path}  ({len(text)} chars)')

    plt.show()
