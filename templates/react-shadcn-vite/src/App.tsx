import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function App() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-6">
      <Card className="w-full max-w-md text-center">
        <CardHeader>
          <CardTitle>Your app will appear here</CardTitle>
          <CardDescription>Describe what you want to build and the team will get to work.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">Built with OpenAtoms</CardContent>
      </Card>
    </main>
  );
}
