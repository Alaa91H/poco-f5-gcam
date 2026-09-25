package dev.alaa.pocof5.camera2probe;

import android.app.Activity;
import android.content.Context;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.SurfaceTexture;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Bundle;
import android.util.Range;
import android.util.Rational;
import android.util.Size;
import android.util.SizeF;
import android.widget.Button;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.lang.reflect.Array;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final String REPORT_NAME = "camera2-report.json";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView statusText;
    private Button generateButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusText = findViewById(R.id.statusText);
        generateButton = findViewById(R.id.generateButton);
        generateButton.setOnClickListener(v -> generateReport());

        if (getIntent() != null && getIntent().getBooleanExtra("autoGenerate", false)) {
            generateReport();
        }
    }

    private void generateReport() {
        generateButton.setEnabled(false);
        statusText.setText("Generating Camera2 report...");

        executor.execute(() -> {
            try {
                JSONObject report = Camera2Report.build(this);
                File output = new File(getFilesDir(), REPORT_NAME);

                try (OutputStreamWriter writer = new OutputStreamWriter(
                        new FileOutputStream(output, false),
                        StandardCharsets.UTF_8)) {
                    writer.write(report.toString(2));
                    writer.write("\n");
                }

                runOnUiThread(() -> {
                    statusText.setText(
                            "Report generated successfully.\n\n" +
                            "Internal path:\n" + output.getAbsolutePath() + "\n\n" +
                            "Use the repository ADB helper to export it."
                    );
                    generateButton.setEnabled(true);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    statusText.setText("Report generation failed:\n" + e);
                    generateButton.setEnabled(true);
                });
            }
        });
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private static final class Camera2Report {
        private Camera2Report() {}

        static JSONObject build(Context context) throws Exception {
            JSONObject root = new JSONObject();
            root.put("schemaVersion", 1);
            root.put("generatedAtUtc", utcNow());
            root.put("device", buildDeviceInfo());

            CameraManager manager =
                    (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);

            String[] ids = manager.getCameraIdList();
            Arrays.sort(ids);
            root.put("cameraIdList", stringArray(ids));

            if (Build.VERSION.SDK_INT >= 30) {
                JSONArray concurrentSets = new JSONArray();
                for (Set<String> set : manager.getConcurrentCameraIds()) {
                    String[] values = set.toArray(new String[0]);
                    Arrays.sort(values);
                    concurrentSets.put(stringArray(values));
                }
                root.put("concurrentCameraIdSets", concurrentSets);
            }

            JSONArray cameras = new JSONArray();
            for (String id : ids) {
                cameras.put(buildCamera(manager, id));
            }
            root.put("cameras", cameras);

            return root;
        }

        private static JSONObject buildDeviceInfo() throws Exception {
            JSONObject device = new JSONObject();
            device.put("manufacturer", Build.MANUFACTURER);
            device.put("brand", Build.BRAND);
            device.put("model", Build.MODEL);
            device.put("device", Build.DEVICE);
            device.put("product", Build.PRODUCT);
            device.put("hardware", Build.HARDWARE);
            device.put("board", Build.BOARD);
            device.put("buildId", Build.ID);
            device.put("display", Build.DISPLAY);
            device.put("fingerprint", Build.FINGERPRINT);
            device.put("androidRelease", Build.VERSION.RELEASE);
            device.put("sdkInt", Build.VERSION.SDK_INT);
            device.put("incremental", Build.VERSION.INCREMENTAL);

            if (Build.VERSION.SDK_INT >= 23) {
                device.put("securityPatch", Build.VERSION.SECURITY_PATCH);
                device.put("baseOs", Build.VERSION.BASE_OS);
            }
            if (Build.VERSION.SDK_INT >= 31) {
                device.put("socManufacturer", Build.SOC_MANUFACTURER);
                device.put("socModel", Build.SOC_MODEL);
            }
            return device;
        }

        private static JSONObject buildCamera(CameraManager manager, String id) throws Exception {
            CameraCharacteristics c = manager.getCameraCharacteristics(id);
            JSONObject camera = new JSONObject();
            camera.put("id", id);

            JSONObject normalized = new JSONObject();
            Integer facing = c.get(CameraCharacteristics.LENS_FACING);
            normalized.put("lensFacing", namedInt(facing, lensFacingName(facing)));

            Integer hardwareLevel = c.get(CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL);
            normalized.put("hardwareLevel", namedInt(
                    hardwareLevel,
                    hardwareLevelName(hardwareLevel)
            ));

            int[] capabilities = c.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES);
            JSONArray capabilityArray = new JSONArray();
            if (capabilities != null) {
                int[] sorted = capabilities.clone();
                Arrays.sort(sorted);
                for (int capability : sorted) {
                    capabilityArray.put(namedInt(capability, capabilityName(capability)));
                }
            }
            normalized.put("capabilities", capabilityArray);

            putValue(normalized, "sensorOrientation",
                    c.get(CameraCharacteristics.SENSOR_ORIENTATION));
            putValue(normalized, "pixelArraySize",
                    c.get(CameraCharacteristics.SENSOR_INFO_PIXEL_ARRAY_SIZE));
            putValue(normalized, "preCorrectionActiveArraySize",
                    c.get(CameraCharacteristics.SENSOR_INFO_PRE_CORRECTION_ACTIVE_ARRAY_SIZE));
            putValue(normalized, "activeArraySize",
                    c.get(CameraCharacteristics.SENSOR_INFO_ACTIVE_ARRAY_SIZE));
            putValue(normalized, "physicalSensorSize",
                    c.get(CameraCharacteristics.SENSOR_INFO_PHYSICAL_SIZE));
            putValue(normalized, "sensitivityRange",
                    c.get(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE));
            putValue(normalized, "maxAnalogSensitivity",
                    c.get(CameraCharacteristics.SENSOR_MAX_ANALOG_SENSITIVITY));
            putValue(normalized, "exposureTimeRange",
                    c.get(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE));
            putValue(normalized, "maxFrameDurationNs",
                    c.get(CameraCharacteristics.SENSOR_INFO_MAX_FRAME_DURATION));
            putValue(normalized, "whiteLevel",
                    c.get(CameraCharacteristics.SENSOR_INFO_WHITE_LEVEL));
            putValue(normalized, "focalLengthsMm",
                    c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS));
            putValue(normalized, "apertures",
                    c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_APERTURES));
            putValue(normalized, "minimumFocusDistanceDiopters",
                    c.get(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE));
            putValue(normalized, "hyperfocalDistanceDiopters",
                    c.get(CameraCharacteristics.LENS_INFO_HYPERFOCAL_DISTANCE));
            putValue(normalized, "focusDistanceCalibration",
                    c.get(CameraCharacteristics.LENS_INFO_FOCUS_DISTANCE_CALIBRATION));
            putValue(normalized, "availableOpticalStabilizationModes",
                    c.get(CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION));
            putValue(normalized, "maxDigitalZoom",
                    c.get(CameraCharacteristics.SCALER_AVAILABLE_MAX_DIGITAL_ZOOM));
            putValue(normalized, "aeTargetFpsRanges",
                    c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES));
            putValue(normalized, "availableAfModes",
                    c.get(CameraCharacteristics.CONTROL_AF_AVAILABLE_MODES));
            putValue(normalized, "availableAeModes",
                    c.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_MODES));
            putValue(normalized, "availableAwbModes",
                    c.get(CameraCharacteristics.CONTROL_AWB_AVAILABLE_MODES));
            putValue(normalized, "availableVideoStabilizationModes",
                    c.get(CameraCharacteristics.CONTROL_AVAILABLE_VIDEO_STABILIZATION_MODES));
            putValue(normalized, "maxNumOutputRaw",
                    c.get(CameraCharacteristics.REQUEST_MAX_NUM_OUTPUT_RAW));
            putValue(normalized, "maxNumOutputProc",
                    c.get(CameraCharacteristics.REQUEST_MAX_NUM_OUTPUT_PROC));
            putValue(normalized, "maxNumOutputProcStalling",
                    c.get(CameraCharacteristics.REQUEST_MAX_NUM_OUTPUT_PROC_STALLING));
            putValue(normalized, "maxNumInputStreams",
                    c.get(CameraCharacteristics.REQUEST_MAX_NUM_INPUT_STREAMS));

            if (Build.VERSION.SDK_INT >= 28) {
                String[] physicalIds = c.getPhysicalCameraIds().toArray(new String[0]);
                Arrays.sort(physicalIds);
                normalized.put("physicalCameraIds", stringArray(physicalIds));
            }

            camera.put("normalized", normalized);

            StreamConfigurationMap map =
                    c.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            camera.put("streamConfiguration", buildStreamConfiguration(map));

            JSONObject allCharacteristics = new JSONObject();
            JSONArray keyNames = new JSONArray();
            for (CameraCharacteristics.Key<?> key : c.getKeys()) {
                String name = key.getName();
                keyNames.put(name);
                Object value = getCharacteristic(c, key);

                if (value instanceof StreamConfigurationMap) {
                    allCharacteristics.put(name, "<see streamConfiguration>");
                } else {
                    allCharacteristics.put(name, toJsonValue(value));
                }
            }
            camera.put("characteristicKeyNames", keyNames);
            camera.put("allCharacteristics", allCharacteristics);

            return camera;
        }

        @SuppressWarnings({"rawtypes", "unchecked"})
        private static Object getCharacteristic(
                CameraCharacteristics characteristics,
                CameraCharacteristics.Key<?> key) {
            return characteristics.get((CameraCharacteristics.Key) key);
        }

        private static JSONObject buildStreamConfiguration(StreamConfigurationMap map)
                throws Exception {
            if (map == null) {
                return new JSONObject().put("available", false);
            }

            JSONObject result = new JSONObject();
            result.put("available", true);

            int[] outputFormats = map.getOutputFormats();
            Arrays.sort(outputFormats);
            JSONArray outputs = new JSONArray();

            for (int format : outputFormats) {
                JSONObject formatObject = new JSONObject();
                formatObject.put("format", namedInt(format, imageFormatName(format)));

                Size[] sizes = map.getOutputSizes(format);
                formatObject.put("sizes", buildOutputSizes(map, format, sizes));

                if (Build.VERSION.SDK_INT >= 23) {
                    Size[] highRes = map.getHighResolutionOutputSizes(format);
                    formatObject.put("highResolutionSizes",
                            highRes == null ? new JSONArray() : sizeArray(highRes));
                }

                outputs.put(formatObject);
            }
            result.put("outputsByFormat", outputs);

            int[] inputFormats = map.getInputFormats();
            Arrays.sort(inputFormats);
            JSONArray inputs = new JSONArray();
            for (int format : inputFormats) {
                JSONObject input = new JSONObject();
                input.put("format", namedInt(format, imageFormatName(format)));
                Size[] sizes = map.getInputSizes(format);
                input.put("sizes", sizes == null ? new JSONArray() : sizeArray(sizes));
                inputs.put(input);
            }
            result.put("inputsByFormat", inputs);

            JSONObject classOutputs = new JSONObject();
            classOutputs.put("SurfaceTexture",
                    nullableSizeArray(map.getOutputSizes(SurfaceTexture.class)));
            classOutputs.put("MediaRecorder",
                    nullableSizeArray(map.getOutputSizes(MediaRecorder.class)));
            result.put("classOutputs", classOutputs);

            if (Build.VERSION.SDK_INT >= 23) {
                Size[] highSpeedSizes = map.getHighSpeedVideoSizes();
                JSONArray highSpeed = new JSONArray();
                if (highSpeedSizes != null) {
                    sortSizes(highSpeedSizes);
                    for (Size size : highSpeedSizes) {
                        JSONObject entry = sizeObject(size);
                        Range<Integer>[] fpsRanges =
                                map.getHighSpeedVideoFpsRangesFor(size);
                        entry.put("fpsRanges", toJsonValue(fpsRanges));
                        highSpeed.put(entry);
                    }
                }
                result.put("highSpeedVideo", highSpeed);
            }

            return result;
        }

        private static JSONArray buildOutputSizes(
                StreamConfigurationMap map,
                int format,
                Size[] sizes) throws Exception {
            JSONArray array = new JSONArray();
            if (sizes == null) {
                return array;
            }

            sortSizes(sizes);
            for (Size size : sizes) {
                JSONObject item = sizeObject(size);
                try {
                    item.put("minFrameDurationNs",
                            map.getOutputMinFrameDuration(format, size));
                } catch (RuntimeException ignored) {
                    item.put("minFrameDurationNs", JSONObject.NULL);
                }
                try {
                    item.put("stallDurationNs",
                            map.getOutputStallDuration(format, size));
                } catch (RuntimeException ignored) {
                    item.put("stallDurationNs", JSONObject.NULL);
                }
                array.put(item);
            }
            return array;
        }

        private static void sortSizes(Size[] sizes) {
            Arrays.sort(sizes, (a, b) -> {
                long areaA = (long) a.getWidth() * a.getHeight();
                long areaB = (long) b.getWidth() * b.getHeight();
                int areaCompare = Long.compare(areaB, areaA);
                if (areaCompare != 0) return areaCompare;
                int widthCompare = Integer.compare(b.getWidth(), a.getWidth());
                if (widthCompare != 0) return widthCompare;
                return Integer.compare(b.getHeight(), a.getHeight());
            });
        }

        private static JSONArray sizeArray(Size[] sizes) throws Exception {
            Size[] copy = sizes.clone();
            sortSizes(copy);
            JSONArray array = new JSONArray();
            for (Size size : copy) {
                array.put(sizeObject(size));
            }
            return array;
        }

        private static JSONArray nullableSizeArray(Size[] sizes) throws Exception {
            return sizes == null ? new JSONArray() : sizeArray(sizes);
        }

        private static JSONObject sizeObject(Size size) throws Exception {
            JSONObject object = new JSONObject();
            object.put("width", size.getWidth());
            object.put("height", size.getHeight());
            object.put("megapixels",
                    Math.round((size.getWidth() * (double) size.getHeight()) / 10000.0) / 100.0);
            return object;
        }

        private static JSONObject namedInt(Integer value, String name) throws Exception {
            JSONObject object = new JSONObject();
            object.put("value", value == null ? JSONObject.NULL : value);
            object.put("name", name == null ? JSONObject.NULL : name);
            return object;
        }

        private static void putValue(JSONObject object, String name, Object value)
                throws Exception {
            object.put(name, toJsonValue(value));
        }

        private static Object toJsonValue(Object value) throws Exception {
            if (value == null) {
                return JSONObject.NULL;
            }

            if (value instanceof Number ||
                    value instanceof Boolean ||
                    value instanceof String) {
                return value;
            }

            if (value instanceof Size) {
                return sizeObject((Size) value);
            }

            if (value instanceof SizeF) {
                SizeF size = (SizeF) value;
                return new JSONObject()
                        .put("width", size.getWidth())
                        .put("height", size.getHeight());
            }

            if (value instanceof Rect) {
                Rect rect = (Rect) value;
                return new JSONObject()
                        .put("left", rect.left)
                        .put("top", rect.top)
                        .put("right", rect.right)
                        .put("bottom", rect.bottom)
                        .put("width", rect.width())
                        .put("height", rect.height());
            }

            if (value instanceof Rational) {
                Rational rational = (Rational) value;
                return new JSONObject()
                        .put("numerator", rational.getNumerator())
                        .put("denominator", rational.getDenominator())
                        .put("decimal", rational.doubleValue());
            }

            if (value instanceof Range) {
                Range<?> range = (Range<?>) value;
                return new JSONObject()
                        .put("lower", toJsonValue(range.getLower()))
                        .put("upper", toJsonValue(range.getUpper()));
            }

            if (value instanceof Set) {
                JSONArray array = new JSONArray();
                String[] values = new String[((Set<?>) value).size()];
                int index = 0;
                for (Object item : (Set<?>) value) {
                    values[index++] = String.valueOf(item);
                }
                Arrays.sort(values);
                for (String item : values) {
                    array.put(item);
                }
                return array;
            }

            Class<?> type = value.getClass();
            if (type.isArray()) {
                JSONArray array = new JSONArray();
                int length = Array.getLength(value);
                for (int i = 0; i < length; i++) {
                    array.put(toJsonValue(Array.get(value, i)));
                }
                return array;
            }

            return String.valueOf(value);
        }

        private static JSONArray stringArray(String[] values) {
            JSONArray array = new JSONArray();
            for (String value : values) {
                array.put(value);
            }
            return array;
        }

        private static String utcNow() {
            SimpleDateFormat format =
                    new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US);
            format.setTimeZone(TimeZone.getTimeZone("UTC"));
            return format.format(new Date());
        }

        private static String lensFacingName(Integer value) {
            if (value == null) return null;
            switch (value) {
                case CameraCharacteristics.LENS_FACING_FRONT:
                    return "FRONT";
                case CameraCharacteristics.LENS_FACING_BACK:
                    return "BACK";
                case CameraCharacteristics.LENS_FACING_EXTERNAL:
                    return "EXTERNAL";
                default:
                    return "UNKNOWN_" + value;
            }
        }

        private static String hardwareLevelName(Integer value) {
            if (value == null) return null;
            switch (value) {
                case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LEGACY:
                    return "LEGACY";
                case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_LIMITED:
                    return "LIMITED";
                case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_FULL:
                    return "FULL";
                case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_3:
                    return "LEVEL_3";
                case CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL_EXTERNAL:
                    return "EXTERNAL";
                default:
                    return "UNKNOWN_" + value;
            }
        }

        private static String capabilityName(int value) {
            switch (value) {
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_BACKWARD_COMPATIBLE:
                    return "BACKWARD_COMPATIBLE";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR:
                    return "MANUAL_SENSOR";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_POST_PROCESSING:
                    return "MANUAL_POST_PROCESSING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_RAW:
                    return "RAW";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_PRIVATE_REPROCESSING:
                    return "PRIVATE_REPROCESSING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_READ_SENSOR_SETTINGS:
                    return "READ_SENSOR_SETTINGS";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_BURST_CAPTURE:
                    return "BURST_CAPTURE";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_YUV_REPROCESSING:
                    return "YUV_REPROCESSING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_DEPTH_OUTPUT:
                    return "DEPTH_OUTPUT";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO:
                    return "CONSTRAINED_HIGH_SPEED_VIDEO";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MOTION_TRACKING:
                    return "MOTION_TRACKING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_LOGICAL_MULTI_CAMERA:
                    return "LOGICAL_MULTI_CAMERA";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_MONOCHROME:
                    return "MONOCHROME";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_SECURE_IMAGE_DATA:
                    return "SECURE_IMAGE_DATA";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_SYSTEM_CAMERA:
                    return "SYSTEM_CAMERA";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_OFFLINE_PROCESSING:
                    return "OFFLINE_PROCESSING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_ULTRA_HIGH_RESOLUTION_SENSOR:
                    return "ULTRA_HIGH_RESOLUTION_SENSOR";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_REMOSAIC_REPROCESSING:
                    return "REMOSAIC_REPROCESSING";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_DYNAMIC_RANGE_TEN_BIT:
                    return "DYNAMIC_RANGE_TEN_BIT";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_STREAM_USE_CASE:
                    return "STREAM_USE_CASE";
                case CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_COLOR_SPACE_PROFILES:
                    return "COLOR_SPACE_PROFILES";
                default:
                    return "UNKNOWN_" + value;
            }
        }

        private static String imageFormatName(int format) {
            switch (format) {
                case ImageFormat.JPEG:
                    return "JPEG";
                case ImageFormat.YUV_420_888:
                    return "YUV_420_888";
                case ImageFormat.RAW_SENSOR:
                    return "RAW_SENSOR";
                case ImageFormat.RAW10:
                    return "RAW10";
                case ImageFormat.RAW12:
                    return "RAW12";
                case ImageFormat.PRIVATE:
                    return "PRIVATE";
                case ImageFormat.DEPTH16:
                    return "DEPTH16";
                case ImageFormat.DEPTH_POINT_CLOUD:
                    return "DEPTH_POINT_CLOUD";
                case ImageFormat.DEPTH_JPEG:
                    return "DEPTH_JPEG";
                case ImageFormat.HEIC:
                    return "HEIC";
                case ImageFormat.YCBCR_P010:
                    return "YCBCR_P010";
                case ImageFormat.JPEG_R:
                    return "JPEG_R";
                default:
                    return "FORMAT_" + format;
            }
        }
    }
}
