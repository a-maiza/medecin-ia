"use client"

import { useState } from "react"
import { CheckCircle2, Circle } from "lucide-react"
import { cn } from "@/lib/utils"
import StepProfil from "./steps/StepProfil"
import StepPatient from "./steps/StepPatient"
import StepConsultation from "./steps/StepConsultation"

const STEPS = [
  { label: "Profil médecin", description: "Spécialité & préférences" },
  { label: "Premier patient", description: "Patient de test" },
  { label: "Consultation démo", description: "Découvrir l'IA" },
]

export default function OnboardingPage() {
  const [currentStep, setCurrentStep] = useState(0)
  const [patientId, setPatientId] = useState<string>("")

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-white py-10">
      <div className="w-full max-w-lg px-8 py-10 bg-white rounded-2xl shadow-md border border-border">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-primary">MédecinAI</h1>
          <p className="text-sm text-muted-foreground mt-1">Bienvenue ! Configurons votre espace.</p>
        </div>

        {/* Step indicator */}
        <nav className="flex items-center justify-between mb-8">
          {STEPS.map((step, index) => {
            const isDone = index < currentStep
            const isActive = index === currentStep
            return (
              <div key={step.label} className="flex items-center">
                <div className="flex flex-col items-center">
                  <div className={cn(
                    "w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold border-2 transition-colors",
                    isDone && "bg-primary border-primary text-primary-foreground",
                    isActive && "border-primary text-primary",
                    !isDone && !isActive && "border-muted text-muted-foreground"
                  )}>
                    {isDone ? <CheckCircle2 className="w-5 h-5" /> : index + 1}
                  </div>
                  <span className={cn(
                    "text-xs mt-1 text-center max-w-[80px] leading-tight",
                    isActive ? "text-primary font-medium" : "text-muted-foreground"
                  )}>
                    {step.label}
                  </span>
                </div>
                {/* Connector line between steps */}
                {index < STEPS.length - 1 && (
                  <div className={cn(
                    "h-0.5 flex-1 mx-2 mb-5 transition-colors",
                    index < currentStep ? "bg-primary" : "bg-border"
                  )} />
                )}
              </div>
            )
          })}
        </nav>

        {/* Step content */}
        {currentStep === 0 && (
          <StepProfil onNext={() => setCurrentStep(1)} />
        )}
        {currentStep === 1 && (
          <StepPatient
            onNext={(id) => {
              setPatientId(id)
              setCurrentStep(2)
            }}
          />
        )}
        {currentStep === 2 && (
          <StepConsultation patientId={patientId} />
        )}

        {/* Step counter */}
        <p className="text-center text-xs text-muted-foreground mt-6">
          Étape {currentStep + 1} sur {STEPS.length}
        </p>
      </div>
    </div>
  )
}
