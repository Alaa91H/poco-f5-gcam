import java.io.File;
import java.util.LinkedHashSet;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.android.tools.smali.dexlib2.DexFileFactory;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.writer.io.FileDataStore;
import com.android.tools.smali.dexlib2.writer.pool.DexPool;

/** Replaces only explicitly reviewed classes in an original DEX. */
public final class MergeCompatibilityDex {
    private MergeCompatibilityDex() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "usage: MergeCompatibilityDex <original.dex> <patch.dex> <output.dex> <descriptors>");
        }
        DexFile original = DexFileFactory.loadDexFile(new File(args[0]), Opcodes.getDefault());
        DexFile patch = DexFileFactory.loadDexFile(new File(args[1]), Opcodes.getDefault());
        Set<String> allowed = new LinkedHashSet<>();
        for (String descriptor : args[3].split(",")) {
            if (!descriptor.isEmpty()) allowed.add(descriptor);
        }
        Map<String, ClassDef> replacements = new HashMap<>();
        for (ClassDef definition : patch.getClasses()) {
            if (!allowed.contains(definition.getType())) {
                throw new IllegalArgumentException("unapproved patched class: " + definition.getType());
            }
            if (replacements.put(definition.getType(), definition) != null) {
                throw new IllegalArgumentException("duplicate patched class: " + definition.getType());
            }
        }
        if (!replacements.keySet().equals(allowed)) {
            throw new IllegalArgumentException("patched class set differs: expected " + allowed + ", found " + replacements.keySet());
        }
        List<ClassDef> merged = new ArrayList<>();
        int replaced = 0;
        for (ClassDef definition : original.getClasses()) {
            ClassDef replacement = replacements.remove(definition.getType());
            merged.add(replacement == null ? definition : replacement);
            if (replacement != null) replaced++;
        }
        int added = replacements.size();
        for (ClassDef definition : replacements.values()) {
            if (!"Lpoco/CameraIds;".equals(definition.getType())) {
                throw new IllegalArgumentException("unexpected new class: " + definition.getType());
            }
            merged.add(definition);
        }
        DexPool output = new DexPool(original.getOpcodes());
        for (ClassDef definition : merged) output.internClass(definition);
        output.writeTo(new FileDataStore(new File(args[2])));
        DexFile check = DexFileFactory.loadDexFile(new File(args[2]), original.getOpcodes());
        if (check.getClasses().size() != original.getClasses().size() + added) {
            throw new IllegalStateException("merged class count mismatch");
        }
        System.out.println("Class count=" + check.getClasses().size()
                + ", replacements=" + replaced + ", added=" + added);
    }
}
