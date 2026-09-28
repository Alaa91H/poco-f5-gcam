import com.reandroid.apk.ApkModule;
import com.reandroid.arsc.chunk.xml.AndroidManifestBlock;
import com.reandroid.arsc.chunk.xml.ResXmlAttribute;
import com.reandroid.arsc.chunk.xml.ResXmlElement;

import java.io.File;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Narrow binary-AXML patcher used by the POCO F5 standalone build.
 *
 * It loads the APK through the ARSCLib classes bundled in the pinned
 * APKEditor fat JAR, changes only AndroidManifest.xml, and streams a new APK.
 * Resources and DEX files are never decoded to an intermediate directory.
 */
public final class OpenClManifestPatcher {
    private static final int ID_ANDROID_NAME = 0x01010003;
    private static final int ID_ANDROID_REQUIRED = 0x0101028e;

    private static final String[] OPENCL_LIBRARIES = {
        "libOpenCL.so",
        "libOpenCL-car.so",
        "libOpenCL-pixel.so",
    };

    private OpenClManifestPatcher() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException(
                    "usage: OpenClManifestPatcher <input.apk> <output.apk>");
        }

        File input = new File(args[0]);
        File output = new File(args[1]);
        if (!input.isFile()) {
            throw new IllegalArgumentException("input APK does not exist: " + input);
        }
        if (output.exists() && !output.delete()) {
            throw new IllegalStateException("could not replace output APK: " + output);
        }

        List<String> added = new ArrayList<>();
        List<String> forcedOptional = new ArrayList<>();

        try (ApkModule module = ApkModule.loadApkFile(input)) {
            module.setLoadDefaultFramework(false);

            AndroidManifestBlock manifest = module.getAndroidManifest();
            if (manifest == null) {
                throw new IllegalStateException("APK has no AndroidManifest.xml");
            }

            ResXmlElement manifestElement = manifest.getManifestElement();
            if (manifestElement == null) {
                throw new IllegalStateException("manifest root element is missing");
            }

            List<ResXmlElement> applications = new ArrayList<>();
            Iterator<ResXmlElement> applicationIterator =
                    manifestElement.getElements("application");
            while (applicationIterator.hasNext()) {
                applications.add(applicationIterator.next());
            }
            if (applications.size() != 1) {
                throw new IllegalStateException(
                        "expected exactly one <application>; found " + applications.size());
            }
            ResXmlElement application = applications.get(0);

            Map<String, ResXmlElement> existing = new LinkedHashMap<>();
            Iterator<ResXmlElement> iterator =
                    application.getElements("uses-native-library");
            while (iterator.hasNext()) {
                ResXmlElement element = iterator.next();
                ResXmlAttribute nameAttribute =
                        element.searchAttributeByResourceId(ID_ANDROID_NAME);
                if (nameAttribute == null) {
                    continue;
                }
                String name = nameAttribute.getValueAsString();
                if (!isOpenClLibrary(name)) {
                    continue;
                }
                if (existing.put(name, element) != null) {
                    throw new IllegalStateException(
                            "duplicate OpenCL <uses-native-library> declaration: " + name);
                }
            }

            for (String name : OPENCL_LIBRARIES) {
                ResXmlElement element = existing.get(name);
                if (element == null) {
                    element = application.newElement("uses-native-library");
                    element.getOrCreateAndroidAttribute("name", ID_ANDROID_NAME)
                            .setValueAsString(name);
                    added.add(name);
                } else {
                    ResXmlAttribute required =
                            element.searchAttributeByResourceId(ID_ANDROID_REQUIRED);
                    if (required == null || required.getValueAsBoolean()) {
                        forcedOptional.add(name);
                    }
                }

                element.getOrCreateAndroidAttribute("required", ID_ANDROID_REQUIRED)
                        .setValueAsBoolean(false);
            }

            module.refreshManifest();
            // Any previous APK signing block is invalid after changing the manifest.
            // The standalone pipeline signs the result with the project key later.
            module.setApkSignatureBlock(null);
            module.writeApk(output);
        }

        System.out.println("status=patched");
        System.out.println("added=" + String.join(",", added));
        System.out.println("forced_optional=" + String.join(",", forcedOptional));
        System.out.println("required=false");
        System.out.println("allow_native_heap_pointer_tagging_changed=false");
    }

    private static boolean isOpenClLibrary(String value) {
        if (value == null) {
            return false;
        }
        for (String candidate : OPENCL_LIBRARIES) {
            if (candidate.equals(value)) {
                return true;
            }
        }
        return false;
    }
}
