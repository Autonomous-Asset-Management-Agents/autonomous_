export const sharedPricingStyles = {
  headerHeight: '340px',
  priceContainerHeight: '90px',
  borderColor: 'rgba(255, 255, 255, 0.32)',
  bgProfessional: 'linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 50%), rgba(255, 255, 255, 0.015)',
  bgInstitutional: 'linear-gradient(180deg, rgba(0, 194, 122, 0.05) 0%, rgba(0, 194, 122, 0) 50%), rgba(255, 255, 255, 0.015)',
  glowProfessional: 'linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.8) 50%, transparent)',
  glowInstitutional: 'linear-gradient(90deg, transparent, #00c27a 50%, transparent)'
};

export interface PricingFeature {
  title: string;
  desc: string;
}

export interface PricingTierData {
  eyebrow: string;
  name: string;
  tag: string;
  price: string;
  priceSub?: string;
  priceMeta?: string;
  features: PricingFeature[];
}

export interface PricingDesign {
  private: {
    Basic: PricingTierData;
    Plus: PricingTierData;
  };
  professional: PricingTierData;
  institutional: PricingTierData;
}

export const pricingData: PricingDesign = {
  private: {
    Basic: {
      eyebrow: "Desktop Version",
      name: "Private",
      tag: "The gateway to sovereign trading. Engineered for individuals seeking to explore advanced AI strategies in a secure, local environment with zero capital exposure.",
      price: "0.00 €",
      priceSub: "/ forever",
      features: [
        { title: "Sovereign Simulation", desc: "Experience the power of high-fidelity paper trading. Master the markets before deploying real capital." },
        { title: "Absolute Data Privacy", desc: "Your intelligence remains your own. All data and keys reside exclusively on your private hardware." },
        { title: "Effortless Onboarding", desc: "A streamlined installation designed for immediate operation. Start your first AI cycle in minutes." }
      ]
    },
    Plus: {
      eyebrow: "Desktop Version",
      name: "Private",
      tag: "Elevate your private trading to institutional-grade execution. The Plus edition unlocks the full consensus framework for active real-market participation.",
      price: "0.99 €",
      priceSub: "/ month",
      features: [
        { title: "Collaborative Intelligence", desc: "Benefit from a 9-agent AI ensemble working in perfect harmony to identify market shifts." },
        { title: "Live Market Bridge", desc: "Direct, high-performance integration with your brokerage for seamless real-time execution." },
        { title: "Hardened Security", desc: "Military-grade encryption via your operating system’s native secure storage perimeter." }
      ]
    }
  },
  professional: {
    eyebrow: "Open Source Version",
    name: "Developer",
    tag: "Optimized for 24/7 operation on your own infrastructure. Leveraging the BORA framework for containerized deployment, it provides full autonomy without a GUI bottleneck.",
    price: "0.00 €",
    priceSub: "/ forever",
    features: [
      { title: "BORA Container Stack", desc: "Pre-configured Docker setup for seamless deployment on AWS, GCP, or private bare-metal servers." },
      { title: "24/7 Headless Autonomy", desc: "Operates as a background service with a minimal resource footprint and maximum uptime." },
      { title: "Cloud-Native Logging", desc: "Integrated streams for audit logs and performance metrics, monitorable via CLI or cloud dashboards." }
    ]
  },
  institutional: {
    eyebrow: "Cloud Native Version",
    name: "Institutional",
    tag: "A sovereign infrastructure stack for professional asset management. Designed for maximum scale, absolute auditability, and corporate-grade security.",
    price: "Custom",
    priceMeta: "Request Quotation",
    features: [
      { title: "Compliance-Ready Auditing", desc: "Tamper-proof, hash-chained logging meeting the highest standards for professional transparency." },
      { title: "Strategic Entity Governance", desc: "Centralized control for complex multi-portfolio management with hardened isolation protocols." },
      { title: "Exclusive Partnership", desc: "Dedicated account management and 24/7 priority engineering support for your critical operations." }
    ]
  }
};
