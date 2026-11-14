import 'dart:isolate';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:image/image.dart' as img;

/// Конвертация YUV420 → JPEG в отдельном isolate, чтобы не дёргать UI.
///
/// Кадр поворачивается на `sensorOrientation` (AR-экран зафиксирован в
/// портретной ориентации), поэтому координаты сервера совпадают с превью.
Future<Uint8List?> encodeCameraFrame(
  CameraImage image, {
  required int rotationDegrees,
  double? targetAspect,
}) {
  try {
    if (image.planes.length < 3) {
      // Некоторые платформы отдают уже готовый JPEG одним plane.
      if (image.planes.length == 1 &&
          image.planes.first.bytes.isNotEmpty &&
          image.format.group == ImageFormatGroup.jpeg) {
        final bytes = Uint8List.fromList(image.planes.first.bytes);
        return Future<Uint8List?>.value(bytes);
      }
      return Future<Uint8List?>.value(null);
    }

    final yPlane = image.planes[0];
    final uPlane = image.planes[1];
    final vPlane = image.planes[2];

    final payload = <String, Object>{
      'width': image.width,
      'height': image.height,
      'rotation': rotationDegrees % 360,
      'targetAspect': targetAspect ?? 0.0,
      'y': yPlane.bytes,
      'yRow': yPlane.bytesPerRow,
      'yPix': yPlane.bytesPerPixel ?? 1,
      'u': uPlane.bytes,
      'uRow': uPlane.bytesPerRow,
      'uPix': uPlane.bytesPerPixel ?? 1,
      'v': vPlane.bytes,
      'vRow': vPlane.bytesPerRow,
      'vPix': vPlane.bytesPerPixel ?? 1,
    };
    return Isolate.run<Uint8List?>(() => _encodeYuv420(payload));
  } catch (_) {
    return Future<Uint8List?>.value(null);
  }
}

Uint8List? _encodeYuv420(Map<String, Object> data) {
  final width = data['width'] as int;
  final height = data['height'] as int;
  if (width <= 0 || height <= 0) return null;

  final yBytes = data['y'] as Uint8List;
  final uBytes = data['u'] as Uint8List;
  final vBytes = data['v'] as Uint8List;
  final yRow = data['yRow'] as int;
  final uRow = data['uRow'] as int;
  final vRow = data['vRow'] as int;
  final yPix = data['yPix'] as int;
  final uPix = data['uPix'] as int;
  final vPix = data['vPix'] as int;

  final rgb = Uint8List(width * height * 3);
  var out = 0;

  for (var y = 0; y < height; y++) {
    final yLine = y * yRow;
    final uvLine = (y >> 1);
    for (var x = 0; x < width; x++) {
      final yIndex = yLine + x * yPix;
      if (yIndex >= yBytes.length) continue;
      final uIndex = uvLine * uRow + (x >> 1) * uPix;
      final vIndex = uvLine * vRow + (x >> 1) * vPix;
      if (uIndex >= uBytes.length || vIndex >= vBytes.length) continue;

      final yValue = yBytes[yIndex];
      final uValue = uBytes[uIndex] - 128;
      final vValue = vBytes[vIndex] - 128;
      final r = yValue + (1.402 * vValue);
      final g = yValue - (0.344136 * uValue) - (0.714136 * vValue);
      final b = yValue + (1.772 * uValue);

      rgb[out++] = r < 0 ? 0 : (r > 255 ? 255 : r.round());
      rgb[out++] = g < 0 ? 0 : (g > 255 ? 255 : g.round());
      rgb[out++] = b < 0 ? 0 : (b > 255 ? 255 : b.round());
    }
  }

  var picture = img.Image.fromBytes(
    width: width,
    height: height,
    bytes: rgb.buffer,
    numChannels: 3,
    order: img.ChannelOrder.rgb,
  );

  final rotation = data['rotation'] as int;
  if (rotation != 0) {
    picture = img.copyRotate(picture, angle: rotation);
  }
  final targetAspect = (data['targetAspect'] as num?)?.toDouble() ?? 0;
  if (targetAspect > 0 && targetAspect.isFinite) {
    picture = _cropToAspect(picture, targetAspect);
  }
  return img.encodeJpg(picture, quality: 80);
}

/// Центральная обрезка под аспект превью: координаты сервера и превью
/// относятся к одному и тому же кадру без letterbox-расхождений.
img.Image _cropToAspect(img.Image image, double targetAspect) {
  final current = image.width / image.height;
  if ((current - targetAspect).abs() < 0.01) return image;
  if (current > targetAspect) {
    final width = math.max(1, (image.height * targetAspect).round());
    final x = math.max(0, ((image.width - width) / 2).round());
    return img.copyCrop(
      image,
      x: x,
      y: 0,
      width: math.min(width, image.width),
      height: image.height,
    );
  }
  final height = math.max(1, (image.width / targetAspect).round());
  final y = math.max(0, ((image.height - height) / 2).round());
  return img.copyCrop(
    image,
    x: 0,
    y: y,
    width: image.width,
    height: math.min(height, image.height),
  );
}

/// Подготовка фото из галереи: EXIF-ориентация, уменьшение длинной стороны
/// до 1600 px с сохранением пропорций, JPEG качества 90.
///
/// Не использует конвертер кадров камеры и не приводит фото к 480×720.
Future<Uint8List> prepareUploadedPhoto(Uint8List bytes) {
  return Isolate.run<Uint8List>(() {
    final decoded = img.decodeImage(bytes);
    if (decoded == null) {
      throw const FormatException('decode_failed');
    }
    var picture = img.bakeOrientation(decoded);
    final longSide = math.max(picture.width, picture.height);
    if (longSide > 1600) {
      if (picture.width >= picture.height) {
        picture = img.copyResize(
          picture,
          width: 1600,
          maintainAspect: true,
          interpolation: img.Interpolation.average,
        );
      } else {
        picture = img.copyResize(
          picture,
          height: 1600,
          maintainAspect: true,
          interpolation: img.Interpolation.average,
        );
      }
    }
    return img.encodeJpg(picture, quality: 90);
  });
}

/// Приводит JPEG с камеры (`takePicture`) к «прямым» пикселям:
/// применяет EXIF-ориентацию и перекодирует с качеством 80.
Future<Uint8List> normalizeCapturedJpeg(Uint8List bytes) {  try {
    return Isolate.run<Uint8List>(() {
      final decoded = img.decodeImage(bytes);
      if (decoded == null) return bytes;
      final baked = img.bakeOrientation(decoded);
      return img.encodeJpg(baked, quality: 80);
    });
  } catch (_) {
    return Future<Uint8List>.value(bytes);
  }
}
