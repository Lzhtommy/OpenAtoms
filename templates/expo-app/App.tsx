import { StatusBar } from "expo-status-bar";
import { SafeAreaView, StyleSheet, Text, View } from "react-native";

export default function App() {
  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.card}>
        <Text style={styles.title}>Your mobile app will appear here</Text>
        <Text style={styles.subtitle}>Built with Expo + React Native. Ask the team to build something.</Text>
      </View>
      <StatusBar style="auto" />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#f6f6f6", alignItems: "center", justifyContent: "center", padding: 24 },
  card: { backgroundColor: "#fff", borderRadius: 16, padding: 24, maxWidth: 360, width: "100%" },
  title: { fontSize: 20, fontWeight: "600", color: "#0c0c0c" },
  subtitle: { marginTop: 8, fontSize: 14, color: "rgba(12,12,12,0.55)" },
});
