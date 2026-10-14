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

/**
 * Pricing data is now built from the locale catalogue (i18n step 1, part 2):
 * the module has no React context, so instead of inline t() calls it takes the
 * translator as an argument. Call sites pass their own `t` from useTranslation.
 */
export type Translate = (key: string) => string;

export const getPricingData = (t: Translate): PricingDesign => ({
  private: {
    Junior: {
      eyebrow: t("landing.pricing.private.junior.eyebrow"),
      name: t("landing.pricing.private.junior.name"),
      tag: t("landing.pricing.private.junior.tag"),
      price: t("landing.pricing.private.junior.price"),
      priceSub: t("landing.pricing.private.junior.priceSub"),
      features: [
        { title: t("landing.pricing.private.junior.f1.title"), desc: t("landing.pricing.private.junior.f1.desc") },
        { title: t("landing.pricing.private.junior.f2.title"), desc: t("landing.pricing.private.junior.f2.desc") },
        { title: t("landing.pricing.private.junior.f3.title"), desc: t("landing.pricing.private.junior.f3.desc") },
      ],
    },
    Senior: {
      eyebrow: t("landing.pricing.private.senior.eyebrow"),
      name: t("landing.pricing.private.senior.name"),
      tag: t("landing.pricing.private.senior.tag"),
      price: t("landing.pricing.private.senior.price"),
      priceSub: t("landing.pricing.private.senior.priceSub"),
      priceMeta: t("landing.pricing.private.senior.priceMeta"),
      features: [
        { title: t("landing.pricing.private.senior.f1.title"), desc: t("landing.pricing.private.senior.f1.desc") },
        { title: t("landing.pricing.private.senior.f2.title"), desc: t("landing.pricing.private.senior.f2.desc") },
        { title: t("landing.pricing.private.senior.f3.title"), desc: t("landing.pricing.private.senior.f3.desc") },
      ],
    },
  },
  professional: {
      eyebrow: t("landing.pricing.professional.eyebrow"),
      name: t("landing.pricing.professional.name"),
      tag: t("landing.pricing.professional.tag"),
      price: t("landing.pricing.professional.price"),
      priceSub: t("landing.pricing.professional.priceSub"),
      features: [
        { title: t("landing.pricing.professional.f1.title"), desc: t("landing.pricing.professional.f1.desc") },
        { title: t("landing.pricing.professional.f2.title"), desc: t("landing.pricing.professional.f2.desc") },
        { title: t("landing.pricing.professional.f3.title"), desc: t("landing.pricing.professional.f3.desc") },
      ],
  },
  institutional: {
      eyebrow: t("landing.pricing.institutional.eyebrow"),
      name: t("landing.pricing.institutional.name"),
      tag: t("landing.pricing.institutional.tag"),
      price: t("landing.pricing.institutional.price"),
      priceMeta: t("landing.pricing.institutional.priceMeta"),
      features: [
        { title: t("landing.pricing.institutional.f1.title"), desc: t("landing.pricing.institutional.f1.desc") },
        { title: t("landing.pricing.institutional.f2.title"), desc: t("landing.pricing.institutional.f2.desc") },
        { title: t("landing.pricing.institutional.f3.title"), desc: t("landing.pricing.institutional.f3.desc") },
      ],
  },
});
