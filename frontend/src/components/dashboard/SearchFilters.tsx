import { MailX } from 'lucide-react';
import { Input } from '../ui/Input';
import { Select } from '../ui/Select';
import { Button } from '../ui/Button';

interface SearchFiltersProps {
  search: string;
  country: string;
  sector: string;
  missingEmail: boolean;
  countries: string[];
  sectors: string[];
  onSearch: (v: string) => void;
  onCountry: (v: string) => void;
  onSector: (v: string) => void;
  onMissingEmail: (v: boolean) => void;
}

export function SearchFilters({
  search, country, sector, missingEmail,
  countries, sectors,
  onSearch, onCountry, onSector, onMissingEmail,
}: SearchFiltersProps) {
  return (
    <div className="flex flex-col sm:flex-row gap-3">
      <div className="flex-1">
        <Input
          icon
          placeholder="Rechercher une entreprise, secteur, pays…"
          value={search}
          onChange={e => onSearch(e.target.value)}
        />
      </div>
      <Select
        value={country}
        onChange={e => onCountry(e.target.value)}
        className="sm:w-44"
      >
        <option value="">Tous les pays</option>
        {countries.map(c => <option key={c} value={c}>{c}</option>)}
      </Select>
      <Select
        value={sector}
        onChange={e => onSector(e.target.value)}
        className="sm:w-56"
      >
        <option value="">Tous les secteurs</option>
        {sectors.map(s => <option key={s} value={s}>{s}</option>)}
      </Select>
      <Button
        type="button"
        variant={missingEmail ? 'primary' : 'secondary'}
        onClick={() => onMissingEmail(!missingEmail)}
        className="shrink-0"
        title="Entreprises avec un site actif mais sans email connu"
      >
        <MailX className="w-4 h-4" />
        Sans email
      </Button>
    </div>
  );
}
