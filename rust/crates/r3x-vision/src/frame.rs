//! An RGB8 frame: decode (JPEG/PNG), bilinear sampling, JPEG encode for Claude.

use std::path::Path;

use crate::VisionError;

#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub width: u32,
    pub height: u32,
    /// Row-major RGB8, `width * height * 3` bytes.
    pub rgb: Vec<u8>,
}

impl Frame {
    pub fn new(width: u32, height: u32, rgb: Vec<u8>) -> Result<Self, VisionError> {
        if rgb.len() != width as usize * height as usize * 3 {
            return Err(VisionError::Image(format!("{}x{} frame needs {} bytes, got {}", width, height, width * height * 3, rgb.len())));
        }
        Ok(Self { width, height, rgb })
    }

    pub fn open(path: impl AsRef<Path>) -> Result<Self, VisionError> {
        let img = image::open(path.as_ref()).map_err(|e| VisionError::Image(format!("{}: {e}", path.as_ref().display())))?;
        let rgb = img.to_rgb8();
        let (w, h) = rgb.dimensions();
        Self::new(w, h, rgb.into_raw())
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, VisionError> {
        let rgb = image::load_from_memory(bytes).map_err(|e| VisionError::Image(e.to_string()))?.to_rgb8();
        let (w, h) = rgb.dimensions();
        Self::new(w, h, rgb.into_raw())
    }

    /// JPEG at `quality` (CantinaOS sends q85 to Claude).
    pub fn to_jpeg(&self, quality: u8) -> Result<Vec<u8>, VisionError> {
        let mut out = Vec::new();
        image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, quality)
            .encode(&self.rgb, self.width, self.height, image::ExtendedColorType::Rgb8)
            .map_err(|e| VisionError::Image(e.to_string()))?;
        Ok(out)
    }

    /// JPEG at `quality`, scaled down (aspect kept) to at most `max_width` wide: what scene
    /// descriptions send to Claude, where image tokens grow with the pixel count.
    pub fn to_jpeg_scaled(&self, quality: u8, max_width: u32) -> Result<Vec<u8>, VisionError> {
        if self.width <= max_width || max_width == 0 {
            return self.to_jpeg(quality);
        }
        let img = image::RgbImage::from_raw(self.width, self.height, self.rgb.clone())
            .ok_or_else(|| VisionError::Image("frame size does not match its pixels".into()))?;
        let h = ((u64::from(self.height) * u64::from(max_width)) / u64::from(self.width)).max(1) as u32;
        let small = image::imageops::resize(&img, max_width, h, image::imageops::FilterType::Triangle);
        let mut out = Vec::new();
        image::codecs::jpeg::JpegEncoder::new_with_quality(&mut out, quality)
            .encode(small.as_raw(), max_width, h, image::ExtendedColorType::Rgb8)
            .map_err(|e| VisionError::Image(e.to_string()))?;
        Ok(out)
    }

    /// Bilinear RGB at a sub-pixel position (pixel centres on integers); black outside, as
    /// OpenCV's `warpAffine` border.
    #[inline]
    pub fn sample(&self, x: f32, y: f32) -> [f32; 3] {
        let (w, h) = (self.width as i64, self.height as i64);
        let (x0, y0) = (x.floor() as i64, y.floor() as i64);
        let (fx, fy) = (x - x0 as f32, y - y0 as f32);
        let px = |xi: i64, yi: i64| -> [f32; 3] {
            if xi < 0 || yi < 0 || xi >= w || yi >= h {
                return [0.0; 3];
            }
            let i = ((yi * w + xi) * 3) as usize;
            [self.rgb[i] as f32, self.rgb[i + 1] as f32, self.rgb[i + 2] as f32]
        };
        let (a, b, c, d) = (px(x0, y0), px(x0 + 1, y0), px(x0, y0 + 1), px(x0 + 1, y0 + 1));
        let mut o = [0.0; 3];
        for k in 0..3 {
            let top = a[k] + (b[k] - a[k]) * fx;
            let bot = c[k] + (d[k] - c[k]) * fx;
            o[k] = top + (bot - top) * fy;
        }
        o
    }
}

#[cfg(test)]
mod tests {
    use super::Frame;

    fn dims(jpeg: &[u8]) -> (u32, u32) {
        let img = image::load_from_memory_with_format(jpeg, image::ImageFormat::Jpeg).unwrap();
        (img.width(), img.height())
    }

    /// Scene photos go to Claude at most `max_width` wide (image tokens ~ w*h/750, so 768 px
    /// is ~ a third of 1280x720's); a smaller frame is sent as it is.
    #[test]
    fn scene_jpegs_are_scaled_to_the_max_width() {
        let big = Frame::new(1280, 720, vec![128; 1280 * 720 * 3]).unwrap();
        assert_eq!(dims(&big.to_jpeg_scaled(85, 768).unwrap()), (768, 432));
        let small = Frame::new(640, 360, vec![128; 640 * 360 * 3]).unwrap();
        assert_eq!(dims(&small.to_jpeg_scaled(85, 768).unwrap()), (640, 360));
    }
}
