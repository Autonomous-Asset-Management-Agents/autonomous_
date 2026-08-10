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
    Junior: PricingTierData;
    Senior: PricingTierData;
  };
  professional: PricingTierData;
  institutional: PricingTierData;
}

export const pricingData: PricingDesign = {
  private: {
    Junior: {
      eyebrow: "Desktop Version",
      name: "Private",
      tag: "The gateway to sovereign trading. Engineered for individuals seeking to explore advanced AI strategies in a secure, local environment with zero capital exposure.",
      price: "0.00 €",
      priceSub: "/ forever",
      features: [
        { title: "Sovereign Simulation", desc: "Experience high-fidelity paper trading. Master your strategies in a secure environment before deploying capital." },
        { title: "AI on Your Desktop", desc: "Run 9 specialized AI agents entirely on your PC, combining self-trained models with European LLMs." },
        { title: "Absolute Data Privacy", desc: "Operate strictly locally with zero telemetry. Your data is never scraped or uploaded." }
      ]
    },
    Senior: {
      eyebrow: "Desktop Version",
      name: "Private",
      tag: "Elevate your private trading to institutional-grade execution. The Senior edition includes all features of Private Junior, while unlocking live market participation and advanced monitoring.",
      price: "0.99 €",
      priceSub: "/ month",
      features: [
        { title: "Live Market Bridge", desc: "Connect directly to your brokerage for live trading, safeguarded by our Iron Dome framework." },
        { title: "Private Daily Intel", desc: "Receive automated portfolio summaries directly to private channels (Slack, Teams, Telegram) with zero data sharing." },
        { title: "Priority Engineering Support", desc: "Access dedicated, fast-response email support to ensure seamless operations during live market execution." }
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
