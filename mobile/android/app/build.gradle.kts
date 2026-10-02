import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release 签名配置：仅当 mobile/android/key.properties 存在时才读取真实签名材料。
// 缺该文件（CI / 他人 clone、未做发布的开发者）时 release 回退 debug 签名，构建不失败；
// 这样仓库里永远不会出现 keystore/密码，clone 即可编译。发布者按
// docs/learn/phase6/P1-release-build.md 生成 keystore 并填写 key.properties 后再出正式包。
val releaseKeystorePropertiesFile = rootProject.file("key.properties")
val releaseKeystoreProperties = Properties()
val releaseSigningEnabled = releaseKeystorePropertiesFile.exists().also { hasFile ->
    if (hasFile) {
        releaseKeystoreProperties.load(FileInputStream(releaseKeystorePropertiesFile))
    }
}

android {
    namespace = "mbdsdr.app"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // applicationId = mbdsdr.app（正式包名，与 iOS PRODUCT_BUNDLE_IDENTIFIER 一致）。
        applicationId = "mbdsdr.app"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        // 仅在 key.properties 存在时填充真实签名；否则该 config 为空，下面 buildTypes 不会选用它。
        create("release") {
            if (releaseSigningEnabled) {
                storeFile = file(releaseKeystoreProperties["storeFile"] as String)
                storePassword = releaseKeystoreProperties["storePassword"] as String
                keyAlias = releaseKeystoreProperties["keyAlias"] as String
                keyPassword = releaseKeystoreProperties["keyPassword"] as String
            }
        }
    }

    buildTypes {
        release {
            // key.properties 存在 → 用正式 release 签名；不存在 → 退回 debug 签名，
            // 保证 clone 后 `flutter run --release` / `flutter build apk --release` 仍可跑（仅本机试跑，勿上架）。
            signingConfig = if (releaseSigningEnabled)
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
